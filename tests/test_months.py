"""A Reminder or a Schedule on days of the month, and a monthly quota."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from llm_gateway import CompletionTurn, ToolCall
from safwa.features.checks.use_cases import create_check
from safwa.features.reminders.agent import (
    resolve_schedule,
)
from safwa.features.reminders.api import (
    ScheduleError,
    describe,
    next_fire,
    resolve,
    schedule_columns,
    schedule_from_payload,
    schedule_of,
    schedule_payload,
)
from safwa.features.reminders.model import Reminder, ScheduleKind
from safwa.features.reminders.use_cases import create_reminder, reconcile_reminders
from safwa.features.schedules.agent import (
    SCHEDULE_FORMS_ERROR,
    ScheduleCompiler,
)
from safwa.features.schedules.api import (
    rule_summary,
    schedule_summary,
)
from safwa.features.schedules.rules import next_slot, windows
from safwa.foundation.workspace import Workspace

TZ = ZoneInfo("Europe/Istanbul")
BERLIN = ZoneInfo("Europe/Berlin")
LISBON = ZoneInfo("Europe/Lisbon")
# A Saturday at noon, local.
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=TZ).astimezone(UTC)
MONTHLY_QUOTA = {"kind": "quota", "period": "month", "count": 3}


def local(*args: int, tz: ZoneInfo = TZ) -> datetime:
    return datetime(*args, tzinfo=tz).astimezone(UTC)


def answering(name, *arguments):
    """A provider that ends a session with the terminal call `name`, with each argument in turn."""
    requests = []

    async def complete(request):
        requests.append(request)
        return CompletionTurn(
            content="The words say a time; I call it.",
            tool_calls=[
                ToolCall(
                    id=str(len(requests)),
                    name=name,
                    arguments_json=json.dumps(arguments[len(requests) - 1]),
                )
            ],
        )

    return SimpleNamespace(complete=complete), requests


def repair_error(requests) -> str:
    repair = json.loads(requests[1].messages[-1]["content"])
    assert repair["retryable"] is True
    return repair["error"]


# --- Reminder -------------------------------------------------------------


async def test_the_session_reads_days_of_the_month():
    """RM-MONTH-037 — tests/brd/reminders.feature"""
    provider, _ = answering(
        "set_reminder_config", {"month_days": [15, 1], "time": "09:00", "last_day": "31.12"}
    )
    schedule = await resolve_schedule(
        provider,
        when="on the 1st and 15th of each month at 9 until the end of the year",
        now=NOW,
        tz=TZ,
    )
    assert (schedule.kind, schedule.month_days) == (ScheduleKind.MONTHLY, (1, 15))
    assert schedule.until == date(2026, 12, 31)





@pytest.mark.parametrize(
    ("month_days", "previous", "following"),
    [
        ([1, 15], None, local(2026, 11, 1, 9, 0)),
        ([1, 15], local(2026, 11, 1, 9, 0), local(2026, 11, 15, 9, 0)),
        ([31], None, local(2026, 10, 31, 9, 0)),
        ([31], local(2026, 10, 31, 9, 0), local(2026, 11, 30, 9, 0)),
        ([30], local(2027, 1, 30, 9, 0), local(2027, 2, 28, 9, 0)),
        ([30], local(2027, 2, 28, 9, 0), local(2027, 3, 30, 9, 0)),
        ([31], local(2028, 1, 31, 9, 0), local(2028, 2, 29, 9, 0)),
    ],
)
def test_days_of_the_month_are_worked_out_in_code(month_days, previous, following):
    """RM-MONTH-037 — tests/brd/reminders.feature"""
    schedule = resolve(month_days=month_days, clock="09:00", now=NOW, tz=TZ)
    assert next_fire(schedule, previous=previous, now=NOW, tz=TZ) == following





@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"month_days": [15, 1], "clock": "09:00"}, "on the 1st and 15th of each month at 09:00"),
        ({"month_days": [31], "clock": "18:00"}, "on the last day of each month at 18:00"),
        (
            {"month_days": [2, 3, 11, 21, 22, 23], "clock": "07:00"},
            "on the 2nd, 3rd, 11th, 21st, 22nd and 23rd of each month at 07:00",
        ),
        (
            {"month_days": [1], "clock": "09:00", "day": "01.12", "until": "3 months", "limit": 3},
            "on the 1st of each month at 09:00, from 01.12.2026 09:00, until 28.02.2027, 3 times",
        ),
    ],
)
def test_the_screens_say_the_days_of_the_month(kwargs, expected):
    """RM-MONTH-037 — tests/brd/reminders.feature"""
    schedule = resolve(now=NOW, tz=TZ, **kwargs)
    assert describe(schedule, tz=TZ, now=NOW) == expected
    assert schedule_from_payload(json.loads(json.dumps(schedule_payload(schedule)))) == schedule
    assert schedule_of(SimpleNamespace(**schedule_columns(schedule))) == schedule











async def test_startup_works_it_out_again_after_a_timezone_move(sessions):
    """RM-MONTH-037 — tests/brd/reminders.feature"""
    schedule = resolve(month_days=[15], clock="09:00", now=NOW, tz=TZ)
    async with sessions() as session:
        reminder = Reminder(
            remind_text="Pay the rent.",
            next_fire_at=next_fire(schedule, previous=None, now=NOW, tz=TZ),
            **schedule_columns(schedule),
        )
        session.add(reminder)
        (await session.get(Workspace, 1)).timezone = LISBON.key
        await session.commit()
        await reconcile_reminders(session, now=NOW)
        await session.commit()
        moved = (await session.get(Reminder, reminder.id)).next_fire_at.astimezone(LISBON)
        assert (moved.day, f"{moved:%H:%M}") == (15, "09:00")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"days": ["Mon"], "month_days": [1], "clock": "09:00"}, "Give days or month_days, not both"),
        ({"month_days": [1], "interval_minutes": 120}, "give time instead of interval_minutes"),
        ({"month_days": [1]}, "month_days need a time"),
        ({"month_days": [1], "in_minutes": 30}, "give no days, month_days, time, first_day or interval"),
    ],
)
def test_days_of_the_month_that_make_no_schedule_are_refused(kwargs, message):
    """RM-SCHEDULE-005 — tests/brd/reminders.feature"""
    with pytest.raises(ScheduleError, match=message):
        resolve(now=NOW, tz=TZ, **kwargs)





async def test_safwa_reads_days_of_the_month_in_ai_reminders(read_views):
    """RM-MONTH-037 — tests/brd/reminders.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        await create_reminder(
            session,
            remind_text="Pay the bills.",
            schedule=resolve(month_days=[1, 15], clock="09:00", now=datetime.now(UTC), tz=TZ),
            tz=TZ,
        )
        await session.commit()
    (row,) = (await runner.run("SELECT schedule_kind, month_days FROM ai_reminders")).rows
    assert row["schedule_kind"] == "monthly" and json.loads(row["month_days"]) == [1, 15]


# --- Schedule -------------------------------------------------------------


async def test_the_scheduler_reads_days_of_the_month():
    """SCH-MONTH-027 — tests/brd/schedules.feature"""
    provider, _ = answering("set_schedule_config", {"month_days": [15, 1]})
    rule, question = await ScheduleCompiler(provider).compile(
        "on the 1st and 15th of each month", NOW, TZ, "check"
    )
    assert question is None and rule["all_day"] is True and rule["timing"]["month_days"] == [1, 15]
    assert rule_summary(rule, TZ) == "On the 1st and 15th of each month, any time that day."

    provider, _ = answering("set_schedule_config", {"month_days": [31], "time": "18:00"})
    rule, _ = await ScheduleCompiler(provider).compile(
        "on the last day of each month at 18:00", NOW, TZ, "check"
    )
    assert "all_day" not in rule
    assert rule_summary(rule, TZ) == "On the last day of each month at 18:00."








async def test_a_monthly_quota_screen_names_its_month(sessions, monkeypatch):
    """SCH-MONTH-027 — tests/brd/schedules.feature"""
    monkeypatch.setattr("safwa.features.schedules.api.utcnow", lambda: local(2026, 11, 10, 9, 0))
    async with sessions() as session:
        check = await create_check(
            session, title="Called home?", schedule="three times a month", schedule_rule=MONTHLY_QUOTA
        )
        assert await schedule_summary(session, check) == "0/3 answered for the month from 01.11.2026."


def test_a_month_left_short_carries_nothing_over():
    """SCH-MONTH-027 — tests/brd/schedules.feature"""
    october = local(2026, 10, 1, 0, 0)
    opens, slot = next_slot(MONTHLY_QUOTA, october, local(2026, 11, 10, 9, 0), 1, TZ)
    assert (opens, slot) == (True, local(2026, 11, 1, 0, 0))
    assert windows(MONTHLY_QUOTA, slot, local(2026, 12, 1, 0, 0), TZ) == [
        (slot, local(2026, 12, 1, 0, 0), 3)
    ]


def test_a_month_covered_in_part_plans_its_share():
    """SCH-WINDOW-002 — tests/brd/schedules.feature"""
    planned = windows(MONTHLY_QUOTA, local(2026, 11, 21, 0, 0), local(2026, 12, 1, 0, 0), TZ)
    assert planned == [(local(2026, 11, 1, 0, 0), local(2026, 12, 1, 0, 0), 1)]





@pytest.mark.parametrize(
    "config",
    [
        {"period": "month", "count": 2, "month_days": [1, 15]},
        {"period": "month", "count": 2, "month_days": [1, 15], "time": "09:00"},
    ],
)
async def test_a_monthly_count_beside_as_many_days_is_those_days(config):
    """SCH-FORM-024 — tests/brd/schedules.feature"""
    provider, requests = answering("set_schedule_config", config)
    rule, question = await ScheduleCompiler(provider).compile(
        "twice a month on the 1st and 15th", NOW, TZ, "check"
    )
    assert question is None and len(requests) == 1
    assert rule["kind"] == "fixed" and rule["timing"]["month_days"] == [1, 15]


@pytest.mark.parametrize(
    "config",
    [
        {"period": "month", "count": 1, "month_days": [1]},
        {"period": "month", "count": 3, "month_days": [1, 15]},
    ],
)
async def test_any_other_monthly_quota_beside_days_is_repaired(config):
    """SCH-FORM-024 — tests/brd/schedules.feature"""
    provider, requests = answering("set_schedule_config", config, {"period": "month", "count": 1})
    rule, _ = await ScheduleCompiler(provider).compile("once a month", NOW, TZ, "check")
    assert SCHEDULE_FORMS_ERROR in repair_error(requests)
    assert rule == {"kind": "quota", "period": "month", "count": 1}


