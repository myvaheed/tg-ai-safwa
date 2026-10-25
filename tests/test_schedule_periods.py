"""An open instance is on the period it is in, and a closing counts for that period."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from safwa.bootstrap.modules import HOOKS
from safwa.constants import WEEKDAY_NAMES
from safwa.features.cards.hooks import SCHEDULE_PLAN_HOOK
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, finish_action
from safwa.features.checks.api import answer_run, newest_answers
from safwa.features.checks.model import Check
from safwa.features.checks.use_cases import create_check, resolve_check, update_check_fields
from safwa.features.reminders.api import Schedule, resolve, schedule_of, schedule_payload
from safwa.features.reminders.hooks import REMINDER_FIRE_HOOK
from safwa.features.reminders.model import ScheduleKind
from safwa.features.schedules.api import entity_reminder, move_on_open_instances, remind_timing
from safwa.features.schedules.hooks import SCHEDULE_PERIOD_HOOK
from safwa.features.schedules.rules import current_slot
from safwa.features.schedules.use_cases import set_remind
from tg_agent_shell.cues.initiatives import TickPoll
from tg_agent_shell.hooks.registry import HookRegistry

TZ = ZoneInfo("Europe/Istanbul")


def local(day: int, hour: int, minute: int = 0) -> datetime:
    """A local time in the week of Monday 19.10.2026."""
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ).astimezone(UTC)


def fixed(schedule: Schedule, **extra) -> dict:
    return {"kind": "fixed", "timing": schedule_payload(schedule), **extra}


def at(monkeypatch, moment: datetime) -> None:
    for module in ("schedules.api", "checks.use_cases", "cards.use_cases"):
        monkeypatch.setattr(f"safwa.features.{module}.utcnow", lambda: moment)


DAILY_AT_7 = fixed(Schedule(ScheduleKind.DAILY, weekdays=WEEKDAY_NAMES, at_time=time(7)))
DAILY_AT_20 = fixed(Schedule(ScheduleKind.DAILY, weekdays=WEEKDAY_NAMES, at_time=time(20)))
EVERY_DAY = fixed(
    Schedule(ScheduleKind.DAILY, weekdays=WEEKDAY_NAMES, at_time=time(23, 59)), all_day=True
)
EVERY_10_MINUTES = fixed(
    resolve(interval_minutes=10, clock="12:00", day="19.10.2026", now=local(19, 8), tz=TZ)
)


def test_a_moment_holds_its_day_and_an_interval_moment_holds_until_the_next():
    """SCH-PERIOD-031 — tests/brd/schedules.feature"""
    assert current_slot(DAILY_AT_20, local(20, 15), TZ) == local(20, 20)
    assert current_slot(DAILY_AT_20, local(20, 21), TZ) == local(20, 20)
    assert current_slot(EVERY_DAY, local(20, 0, 1), TZ) == local(20, 23, 59)
    mondays_and_thursdays = fixed(
        Schedule(ScheduleKind.WEEKLY, weekdays=("Mon", "Thu"), at_time=time(9))
    )
    assert current_slot(mondays_and_thursdays, local(21, 12), TZ) == local(19, 9)
    assert current_slot(EVERY_10_MINUTES, local(19, 12, 25), TZ) == local(19, 12, 20)
    quota = {"kind": "quota", "period": "week", "count": 3}
    assert current_slot(quota, local(21, 12), TZ) == local(19, 0)
    assert current_slot({"kind": "after_completion"}, local(21, 12), TZ) is None
    # Before the Schedule's first day, its first period.
    assert current_slot(EVERY_10_MINUTES, local(19, 11), TZ) == local(19, 12)
    from_friday = fixed(
        resolve(days=list(WEEKDAY_NAMES), clock="20:00", day="23.10.2026", now=local(19, 8), tz=TZ)
    )
    assert current_slot(from_friday, local(20, 21), TZ) == local(23, 20)


async def test_a_check_answered_before_its_moment_counts_for_its_day(sessions, monkeypatch):
    """SCH-PERIOD-031 — tests/brd/schedules.feature"""
    at(monkeypatch, local(19, 21))
    async with sessions() as session:
        check = await create_check(
            session, title="Took the pills?", schedule="daily 20:00", schedule_rule=DAILY_AT_20
        )
        at(monkeypatch, local(19, 21, 30))
        _, tuesday = await resolve_check(session, check.id, "passed")
        assert check.period_start == local(19, 20) and tuesday.period_start == local(20, 20)

        at(monkeypatch, local(20, 15))
        _, wednesday = await resolve_check(session, tuesday.id, "passed")
        assert tuesday.period_start == local(20, 20) and wednesday.period_start == local(21, 20)
        # Answered on Tuesday again, it is Tuesday's too, and takes nothing from Wednesday.
        at(monkeypatch, local(20, 18))
        _, following = await resolve_check(session, wednesday.id, "missed")
        assert wednesday.period_start == local(20, 20) and following.period_start == local(21, 20)


async def test_an_action_left_on_monday_is_tuesdays_and_its_done_leaves_wednesday(
    sessions, monkeypatch
):
    """SCH-PERIOD-031 — tests/brd/schedules.feature"""
    at(monkeypatch, local(19, 10))
    async with sessions() as session:
        action = await create_card(
            session, kind="action", title="Stretch", schedule="every day", schedule_rule=EVERY_DAY
        )
        assert action.period_start == local(19, 23, 59)
        await move_on_open_instances(session, now=local(19, 23))
        assert action.period_start == local(19, 23, 59)

        await move_on_open_instances(session, now=local(20, 0, 1))
        # The same Action is Tuesday's, and Monday got no answer.
        assert action.period_start == local(20, 23, 59) and action.completed_at is None

        at(monkeypatch, local(20, 10))
        result = await finish_action(session, action.id)
        wednesday = await session.get(Card, result.successor_ids[0])
        assert action.period_start == local(20, 23, 59)
        assert wednesday.period_start == local(21, 23, 59)


async def test_an_unanswered_check_moves_on_and_its_answer_counts_where_it_is(
    sessions, monkeypatch
):
    """SCH-PERIOD-031 — tests/brd/schedules.feature"""
    at(monkeypatch, local(19, 11))
    async with sessions() as session:
        check = await create_check(
            session,
            title="Sitting straight?",
            schedule="every 10 minutes",
            schedule_rule=EVERY_10_MINUTES,
        )
        assert check.period_start == local(19, 12)
        await move_on_open_instances(session, now=local(19, 12, 10))
        assert check.period_start == local(19, 12, 10) and check.outcome is None
        await move_on_open_instances(session, now=local(19, 12, 25))
        assert check.period_start == local(19, 12, 20)

        at(monkeypatch, local(19, 12, 25))
        _, following = await resolve_check(session, check.id, "passed")
        assert check.period_start == local(19, 12, 20)
        assert following.period_start == local(19, 12, 30)


async def test_a_morning_left_unanswered_drops_out_and_the_row_goes_on(sessions, monkeypatch):
    """SCH-PERIOD-031 — tests/brd/schedules.feature"""
    at(monkeypatch, local(19, 6))
    async with sessions() as session:
        check = await create_check(
            session, title="Meditated?", schedule="every day at 7", schedule_rule=DAILY_AT_7
        )
        series = check.id
        for day in (19, 20, 21):
            at(monkeypatch, local(day, 8))
            _, check = await resolve_check(session, check.id, "passed")
        # Thursday goes by unanswered: on Friday the same Check is Friday's.
        await move_on_open_instances(session, now=local(23, 0, 1))
        assert check.period_start == local(23, 7) and check.outcome is None
        at(monkeypatch, local(23, 9))
        _, check = await resolve_check(session, check.id, "passed")
        at(monkeypatch, local(24, 8))
        await resolve_check(session, check.id, "passed")

        ids = list(await session.scalars(select(Check.id).where(Check.series_id == series)))
        answers = await newest_answers(session, ids)
        assert [row.resolved_at for row in answers] == [
            local(24, 8), local(23, 9), local(21, 8), local(20, 8), local(19, 8)
        ]
        slots = sorted([(await session.get(Check, row.id)).period_start for row in answers])
        assert slots == [local(day, 7) for day in (19, 20, 21, 23, 24)]
        # Only a Missed ends a row: five Passed in a row.
        assert len(answer_run(answers, "passed")) == 5


async def test_remind_stays_as_its_instance_moves_on_and_follows_the_answer(
    sessions, monkeypatch
):
    """SCH-REMIND-020 — tests/brd/schedules.feature"""
    # A year ahead of the real clock, which a Reminder's first fire reads.
    start = (datetime.now(TZ) + timedelta(days=365)).date()
    monday = start - timedelta(days=start.weekday())

    def day(offset: int, hour: int, minute: int = 0) -> datetime:
        moment = datetime.combine(monday + timedelta(days=offset), time(hour, minute), TZ)
        return moment.astimezone(UTC)

    def now(moment: datetime) -> None:
        at(monkeypatch, moment)
        monkeypatch.setattr("safwa.features.schedules.use_cases.utcnow", lambda: moment)

    now(day(0, 19))
    async with sessions() as session:
        check = await create_check(
            session, title="Took the pills?", schedule="daily 20:00", schedule_rule=DAILY_AT_20
        )
        await set_remind(session, check, 0)
        reminder = await entity_reminder(session, check)
        assert reminder.next_fire_at == day(0, 20)
        # It rang on Monday, and the tick moved it on to Tuesday.
        reminder.next_fire_at = day(1, 20)

        await move_on_open_instances(session, now=day(1, 0, 1))
        assert check.period_start == day(1, 20)
        assert (reminder.item_id, reminder.next_fire_at) == (check.id, day(1, 20))
        # Its timing moved with the instance, so a rename leaves it alone.
        assert schedule_of(reminder) == remind_timing(check, day(1, 10), 0)
        version = reminder.version
        await update_check_fields(session, check.id, {"title": "Took the evening pills?"})
        assert (reminder.version, reminder.next_fire_at) == (version + 1, day(1, 20))
        assert "Took the evening pills?" in reminder.remind_text

        now(day(1, 21))
        _, wednesday = await resolve_check(session, check.id, "passed")
        assert (reminder.item_id, reminder.next_fire_at) == (wednesday.id, day(2, 20))


async def test_the_tick_moves_an_open_instance_on_before_the_plan_and_remind_read_it(
    sessions, monkeypatch
):
    """SCH-PERIOD-031 — tests/brd/schedules.feature"""
    at(monkeypatch, local(19, 10))
    async with sessions() as session:
        action = await create_card(
            session, kind="action", title="Stretch", schedule="every day", schedule_rule=EVERY_DAY
        )
        await session.commit()
        action_id = action.id
    midnight = local(20, 0, 1)
    at(monkeypatch, midnight)
    poll = TickPoll(
        HookRegistry.of((SCHEDULE_PERIOD_HOOK,), owners=frozenset({"schedules"})),
        sessions,
        resources=SimpleNamespace(),
        timezone="Europe/Istanbul",
        now=midnight,
    )

    await poll.look(midnight + timedelta(seconds=30))

    async with sessions() as session:
        assert (await session.get(Card, action_id)).period_start == local(20, 23, 59)
    order = [HOOKS.index(hook) for hook in (SCHEDULE_PERIOD_HOOK, SCHEDULE_PLAN_HOOK, REMINDER_FIRE_HOOK)]
    assert order == sorted(order)
