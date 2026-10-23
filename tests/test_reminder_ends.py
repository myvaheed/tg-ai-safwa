"""A repeating Reminder that ends on a last day or after a total."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.constants import WEEKDAY_NAMES
from safwa.features.reminders.agent import resolve_schedule
from safwa.features.reminders.api import (
    BOUNDS_NEED_REPEAT,
    NEAREST_START_DAYS,
    REMINDER_CATCHUP_GRACE_MINUTES,
    UNTIL_BEFORE_FIRST,
    UNTIL_FORMS,
    ScheduleError,
    describe,
    next_fire,
    parse_until,
    resolve,
    roll_forward,
    schedule_columns,
    schedule_from_payload,
    schedule_payload,
    start_day,
)
from safwa.features.reminders.firing import LAST_FIRE, tick
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.telegram.screens import _detail_text
from safwa.features.reminders.use_cases import reconcile_reminders, reschedule_reminder
from tg_agent_shell.cues.model import Cue

TZ = ZoneInfo("Europe/Istanbul")
# A Saturday at noon, local.
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=TZ).astimezone(UTC)
TODAY = date(2026, 10, 17)
EVERY_DAY = list(WEEKDAY_NAMES)


@pytest.mark.parametrize(
    ("raw", "start", "last"),
    [
        ("30.11.2026", TODAY, date(2026, 11, 30)),
        ("30.11", TODAY, date(2026, 11, 30)),
        ("10.10", TODAY, date(2027, 10, 10)),
        ("Fri", TODAY, date(2026, 10, 23)),
        ("Sat", TODAY, TODAY),
        ("2 weeks", TODAY, date(2026, 10, 30)),
        ("30 days", TODAY, date(2026, 11, 15)),
        ("1 week", TODAY, date(2026, 10, 23)),
        ("1 month", date(2026, 10, 19), date(2026, 11, 18)),
        ("1 month", date(2027, 1, 31), date(2027, 2, 28)),
        ("1 month", date(2026, 3, 1), date(2026, 3, 31)),
        ("1 year", date(2026, 11, 1), date(2027, 10, 31)),
        ("2 months", date(2026, 11, 6), date(2027, 1, 5)),
    ],
)
def test_until_is_the_last_day_worked_out_in_code(raw, start, last):
    """RM-END-036 — tests/brd/reminders.feature"""
    assert parse_until(raw, start) == last


def test_until_in_another_form_names_the_forms():
    """RM-END-036 — tests/brd/reminders.feature"""
    with pytest.raises(ScheduleError, match=UNTIL_FORMS):
        parse_until("the end of time", TODAY)


def test_a_one_time_date_without_a_year_is_the_next_one_still_ahead():
    """RM-END-036 — tests/brd/reminders.feature"""
    passed = resolve(clock="09:00", day="17.10", now=NOW, tz=TZ)
    assert passed.anchor_at.astimezone(TZ).date() == date(2027, 10, 17)


def test_a_repeats_start_without_a_year_is_the_one_nearest_today():
    """RM-END-036 — tests/brd/reminders.feature"""
    schedule = resolve(month_days=[15], clock="12:00", day="15.10", until="30.04", now=NOW, tz=TZ)
    assert schedule.anchor_at.astimezone(TZ).date() == date(2026, 10, 15)
    first = next_fire(schedule, previous=None, now=NOW, tz=TZ)
    assert first == datetime(2026, 11, 15, 12, 0, tzinfo=TZ)
    assert schedule.until == date(2027, 4, 30)
    december = datetime(2026, 12, 28, 9, 0, tzinfo=TZ)
    later = resolve(days=EVERY_DAY, clock="09:00", day="10.01", now=december, tz=TZ)
    assert later.anchor_at.astimezone(TZ).date() == date(2027, 1, 10)
    edge = TODAY - timedelta(days=NEAREST_START_DAYS)
    assert start_day(f"{edge:%d.%m}", None, NOW, TZ, repeats=True) == edge
    beyond = edge - timedelta(days=1)
    assert start_day(f"{beyond:%d.%m}", None, NOW, TZ, repeats=True) == beyond.replace(year=2027)


def test_how_long_counts_from_the_start_or_from_today():
    """RM-END-036 — tests/brd/reminders.feature"""
    from_today = resolve(days=EVERY_DAY, clock="07:00", until="2 weeks", now=NOW, tz=TZ)
    assert from_today.until == date(2026, 10, 30)
    from_start = resolve(
        days=EVERY_DAY, clock="10:00", day="01.11.2026", until="1 year", now=NOW, tz=TZ
    )
    assert from_start.until == date(2027, 10, 31)


def test_it_never_fires_after_its_last_day():
    """RM-END-036 — tests/brd/reminders.feature"""
    schedule = resolve(days=EVERY_DAY, clock="09:00", until="Mon", now=NOW, tz=TZ)
    last = datetime(2026, 10, 19, 9, 0, tzinfo=TZ).astimezone(UTC)
    assert next_fire(schedule, previous=last - timedelta(days=1), now=NOW, tz=TZ) == last
    assert next_fire(schedule, previous=last, now=NOW, tz=TZ) is None
    assert roll_forward(schedule, previous=last, now=last, tz=TZ) is None

    interval = resolve(interval_minutes=180, until="Sat", now=NOW, tz=TZ)
    late = datetime(2026, 10, 17, 21, 0, tzinfo=TZ).astimezone(UTC)
    assert next_fire(interval, previous=late, now=NOW, tz=TZ) is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"clock": "15:00", "day": "20.10.2026", "until": "Fri"}, BOUNDS_NEED_REPEAT),
        ({"in_minutes": 30, "limit": 3}, BOUNDS_NEED_REPEAT),
        ({"days": ["Mon"], "clock": "10:00", "until": "Sat"}, UNTIL_BEFORE_FIRST),
        ({"days": ["Mon"], "clock": "10:00", "day": "02.11.2026", "until": "30.10.2026"}, UNTIL_BEFORE_FIRST),
    ],
)
def test_a_bound_that_cannot_end_a_repeat_is_refused(kwargs, message):
    """RM-END-036 — tests/brd/reminders.feature"""
    with pytest.raises(ScheduleError, match=message):
        resolve(now=NOW, tz=TZ, **kwargs)


def test_the_screens_say_the_last_day_and_the_total():
    """RM-END-036 — tests/brd/reminders.feature"""
    schedule = resolve(
        days=EVERY_DAY, clock="09:00",
        until="30.11", limit=10, now=NOW, tz=TZ,
    )
    assert describe(schedule, tz=TZ, now=NOW) == "every day at 09:00, until 30.11.2026, 10 times"
    assert schedule_from_payload(json.loads(json.dumps(schedule_payload(schedule)))) == schedule


async def test_the_session_reads_the_last_day_and_the_total():
    """RM-END-036 — tests/brd/reminders.feature"""

    async def complete(request):
        arguments = {"interval_minutes": 180, "last_day": "Fri", "limit": 20}
        return CompletionTurn(
            content="",
            tool_calls=[ToolCall(id="1", name="set_reminder_config", arguments_json=json.dumps(arguments))],
        )

    schedule = await resolve_schedule(
        SimpleNamespace(complete=complete),
        when="every 3 hours until Friday, 20 times",
        instruction="Drink water.",
        now=NOW,
        tz=TZ,
        items_series=None,  # a time names no items
    )
    assert (schedule.interval_minutes, schedule.until, schedule.total) == (180, date(2026, 10, 23), 20)





async def _add(sessions, schedule, *, due, fired=0) -> int:
    async with sessions() as session:
        reminder = Reminder(
            instruction="Stretch.", next_fire_at=due, fired=fired, **schedule_columns(schedule)
        )
        session.add(reminder)
        await session.commit()
        return reminder.id


async def _said(sessions) -> list[str]:
    async with sessions() as session:
        texts = [cue.text for cue in await session.scalars(select(Cue).order_by(Cue.id))]
        for cue in await session.scalars(select(Cue)):
            await session.delete(cue)
        await session.commit()
        return texts


async def test_it_fires_its_total_and_then_deletes_itself(sessions):
    """RM-END-036 — tests/brd/reminders.feature"""
    schedule = resolve(interval_minutes=60, limit=2, now=NOW, tz=TZ)
    reminder_id = await _add(sessions, schedule, due=NOW)

    assert await tick(sessions, tz=TZ, now=NOW) is True
    (first,) = await _said(sessions)
    assert LAST_FIRE not in first
    async with sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        assert reminder.fired == 1
        assert _detail_text(reminder, "Stretch.", tz=TZ, now=NOW, finds=None).count(
            "1 of 2 fired. Deletes itself after its last time."
        ) == 1
        due = reminder.next_fire_at

    assert await tick(sessions, tz=TZ, now=due) is True
    (second,) = await _said(sessions)
    assert f"   {LAST_FIRE}" in second
    async with sessions() as session:
        assert await session.get(Reminder, reminder_id) is None


async def test_its_last_firing_on_its_last_day_says_so(sessions):
    """RM-END-036 — tests/brd/reminders.feature"""
    schedule = resolve(days=EVERY_DAY, clock="13:00", until="Sat", now=NOW, tz=TZ)
    due = datetime(2026, 10, 17, 13, 0, tzinfo=TZ).astimezone(UTC)
    reminder_id = await _add(sessions, schedule, due=due)

    assert await tick(sessions, tz=TZ, now=due) is True
    (words,) = await _said(sessions)
    assert LAST_FIRE in words and "until 17.10.2026" in words
    async with sessions() as session:
        assert await session.get(Reminder, reminder_id) is None


async def test_one_that_moves_on_past_its_last_day_deletes_itself_quietly(sessions):
    """RM-END-036 — tests/brd/reminders.feature"""
    schedule = resolve(interval_minutes=60, until="Sat", now=NOW, tz=TZ)
    late = datetime(2026, 10, 17, 22, 0, tzinfo=TZ).astimezone(UTC)
    later = late + timedelta(minutes=REMINDER_CATCHUP_GRACE_MINUTES + 60)
    stale = await _add(sessions, schedule, due=late)
    assert await tick(sessions, tz=TZ, now=later) is False
    assert await _said(sessions) == []

    restarted = await _add(sessions, schedule, due=late)
    async with sessions() as session:
        await reconcile_reminders(session, now=later)
        await session.commit()
        assert await session.get(Reminder, stale) is None
        assert await session.get(Reminder, restarted) is None


async def test_a_new_schedule_counts_its_total_from_zero(sessions):
    """RM-END-036 — tests/brd/reminders.feature"""
    schedule = resolve(interval_minutes=60, limit=5, now=NOW, tz=TZ)
    reminder_id = await _add(sessions, schedule, due=NOW + timedelta(hours=1), fired=3)
    async with sessions() as session:
        reminder = await reschedule_reminder(session, reminder_id, schedule=schedule, tz=TZ)
        assert reminder.fired == 0
        assert reminder.total == 5
