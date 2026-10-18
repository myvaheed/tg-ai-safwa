"""A repeating Schedule that ends on a last day, and a quota that starts on a later one."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from hook_helpers import changes_of

from llm_gateway import CompletionTurn, ToolCall
from safwa.constants import WEEKDAY_NAMES
from safwa.features.cards.use_cases import create_card, finish_action
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import create_check, resolve_check
from safwa.features.reminders.api import (
    BOUNDS_NEED_REPEAT,
    UNTIL_BEFORE_FIRST,
    resolve,
    schedule_from_payload,
    schedule_payload,
)
from safwa.features.schedules.agent import (
    SCHEDULE_FORMS_ERROR,
    SCHEDULE_PARSER_PROMPT,
    ScheduleCompiler,
)
from safwa.features.schedules.api import (
    SCHEDULE_ENDED,
    remaining_occurrences,
    rule_summary,
    schedule_summary,
)
from safwa.features.schedules.hooks import end_request
from safwa.features.schedules.rules import period_start
from tg_agent_shell.foundation.changes import Committed

TZ = ZoneInfo("Europe/Istanbul")
# A Saturday at noon: next Monday is 19.10.2026.
SUBMITTED = datetime(2026, 10, 17, 12, 0, tzinfo=TZ)
EVERY_DAY = list(WEEKDAY_NAMES)


def scripted(*arguments):
    """A provider whose Scheduler session answers set_schedule_config with each in turn."""
    requests = []

    async def complete(request):
        requests.append(request)
        return CompletionTurn(
            content="",
            tool_calls=[
                ToolCall(
                    id=str(len(requests)),
                    name="set_schedule_config",
                    arguments_json=json.dumps(arguments[len(requests) - 1]),
                )
            ],
        )

    return SimpleNamespace(complete=complete), requests


def repair_error(requests) -> str:
    repair = json.loads(requests[1].messages[-1]["content"])
    assert repair["retryable"] is True
    return repair["error"]


def today() -> datetime:
    return datetime.now(UTC).astimezone(TZ)


async def test_the_scheduler_reads_a_last_day():
    """SCH-END-025 — tests/brd/schedules.feature"""
    assert "'every Monday until 15 December': days=[Mon], last_day=15.12." in SCHEDULE_PARSER_PROMPT
    provider, _ = scripted({"days": EVERY_DAY, "time": "21:00", "last_day": "30 days"})
    rule, question = await ScheduleCompiler(provider).compile(
        "every day at 21:00 for 30 days", SUBMITTED, TZ, "check"
    )
    assert question is None and rule["until"] == "2026-11-15"
    assert schedule_from_payload(rule["timing"]).until is None
    assert rule_summary(rule, TZ) == "Every day at 21:00, until 15.11.2026."

    provider, _ = scripted({"period": "day", "count": 2, "last_day": "1 week"})
    rule, _ = await ScheduleCompiler(provider).compile("twice a day for a week", SUBMITTED, TZ, "check")
    assert rule == {"kind": "quota", "period": "day", "count": 2, "until": "2026-10-23"}


async def test_a_last_day_on_a_one_time_schedule_goes_back_to_repair():
    """SCH-END-025 — tests/brd/schedules.feature"""
    once = {"first_day": "20.10.2026", "time": "15:00"}
    provider, requests = scripted({**once, "last_day": "Fri"}, once)
    rule, question = await ScheduleCompiler(provider).compile(
        "on 20 October at 15:00", SUBMITTED, TZ, "check"
    )
    assert BOUNDS_NEED_REPEAT in repair_error(requests)
    assert question is None and "until" not in rule


async def test_a_last_day_before_its_first_time_goes_back_to_repair():
    """SCH-END-025 — tests/brd/schedules.feature"""
    provider, requests = scripted({"days": ["Mon"], "last_day": "Sat"}, {"days": ["Mon"]})
    rule, question = await ScheduleCompiler(provider).compile(
        "every Monday until Saturday", SUBMITTED, TZ, "check"
    )
    assert UNTIL_BEFORE_FIRST in repair_error(requests)
    assert question is None and "until" not in rule


async def test_no_instance_opens_after_the_last_day(sessions):
    """SCH-END-025 — tests/brd/schedules.feature"""
    last = today().date()
    async with sessions() as session:
        ending = await create_check(
            session,
            title="Stretch?",
            schedule="once a day until today",
            schedule_rule={"kind": "quota", "period": "day", "count": 1, "until": last.isoformat()},
        )
        assert "Last day" in await schedule_summary(session, ending)
        _, after = await resolve_check(session, ending.id, CheckOutcome.PASSED)
        assert after is None

        going = await create_check(
            session,
            title="Posture?",
            schedule="once a day until tomorrow",
            schedule_rule={
                "kind": "quota", "period": "day", "count": 1,
                "until": (last + timedelta(days=1)).isoformat(),
            },
        )
        _, after = await resolve_check(session, going.id, CheckOutcome.MISSED)
        assert after is not None

        timing = resolve(days=EVERY_DAY, clock="23:59", now=datetime.now(UTC), tz=TZ)
        fixed = await create_card(
            session,
            kind="action",
            title="Read",
            schedule="every day until today",
            schedule_rule={
                "kind": "fixed", "timing": schedule_payload(timing), "all_day": True,
                "until": last.isoformat(),
            },
        )
        assert (await finish_action(session, fixed.id)).successor_ids == []
        assert changes_of(session, SCHEDULE_ENDED) == [
            Committed(SCHEDULE_ENDED, ending.id, {"type": "check", "reason": "last_day"}),
            Committed(SCHEDULE_ENDED, fixed.id, {"type": "card", "reason": "last_day"}),
        ]
        await session.commit()

        request = await end_request(session, [["check", ending.id, "last_day"]])
        assert f"- Check #{ending.id} «Stretch?», last day {last:%d.%m.%Y}" in request
        assert "or reached its last day" in request


async def test_planning_counts_no_work_after_the_last_day(sessions):
    """SCH-END-025 — tests/brd/schedules.feature"""
    first = today().date()
    async with sessions() as session:
        ending = await create_card(
            session,
            kind="action",
            title="Water",
            schedule="twice a day until tomorrow",
            schedule_rule={
                "kind": "quota", "period": "day", "count": 2,
                "until": (first + timedelta(days=1)).isoformat(),
            },
        )
        assert await remaining_occurrences(session, ending, first, first + timedelta(days=5)) == 4


async def test_planning_counts_no_work_before_a_quota_starts(sessions):
    """SCH-START-026 — tests/brd/schedules.feature"""
    first = today().date()
    async with sessions() as session:
        later = await create_card(
            session,
            kind="action",
            title="Walk",
            schedule="once a day from in three days",
            schedule_rule={
                "kind": "quota", "period": "day", "count": 1,
                "start": (first + timedelta(days=3)).isoformat(),
            },
        )
        assert await remaining_occurrences(session, later, first, first + timedelta(days=5)) == 3


async def test_the_scheduler_reads_a_quota_from_a_later_day():
    """SCH-START-026 — tests/brd/schedules.feature"""
    assert (
        "'twice a week from Wednesday for 2 months': period=week, count=2, first_day=Wed, "
        "last_day=2 months." in SCHEDULE_PARSER_PROMPT
    )
    provider, _ = scripted({"period": "week", "count": 3, "first_day": "Mon", "last_day": "1 month"})
    rule, question = await ScheduleCompiler(provider).compile(
        "3 times a week from Monday for a month", SUBMITTED, TZ, "check"
    )
    assert question is None and rule == {
        "kind": "quota", "period": "week", "count": 3, "start": "2026-10-19", "until": "2026-11-18",
    }
    assert rule_summary(rule, TZ) == "3 per week from 19.10.2026, until 18.11.2026."


async def test_a_quota_start_without_a_year_is_the_one_nearest_today():
    """SCH-START-026 — tests/brd/schedules.feature"""
    provider, _ = scripted({"period": "week", "count": 3, "first_day": "12.10"})
    rule, question = await ScheduleCompiler(provider).compile(
        "3 times a week from 12.10", SUBMITTED, TZ, "check"
    )
    assert question is None and rule["start"] == "2026-10-12"


async def test_all_seven_weekdays_beside_a_quota_are_the_quota_alone():
    """SCH-FORM-024 — tests/brd/schedules.feature"""
    provider, requests = scripted(
        {"period": "week", "count": 3, "days": EVERY_DAY, "first_day": "Mon", "last_day": "1 month"}
    )
    rule, question = await ScheduleCompiler(provider).compile(
        "3 times a week from Monday for a month", SUBMITTED, TZ, "check"
    )
    assert question is None and len(requests) == 1
    assert rule == {
        "kind": "quota", "period": "week", "count": 3, "start": "2026-10-19", "until": "2026-11-18",
    }


async def test_a_date_beside_repetition_after_completion_goes_back_to_repair():
    """SCH-START-026 — tests/brd/schedules.feature"""
    provider, requests = scripted(
        {"after_completion": True, "first_day": "Mon"}, {"after_completion": True}
    )
    rule, _ = await ScheduleCompiler(provider).compile(
        "after I finish it, from Monday", SUBMITTED, TZ, "check"
    )
    assert SCHEDULE_FORMS_ERROR in repair_error(requests)
    assert rule == {"kind": "after_completion"}


async def test_a_quota_opens_and_counts_in_its_first_week(sessions):
    """SCH-START-026 — tests/brd/schedules.feature"""
    start = today().date() + timedelta(days=14)
    first_week = period_start(datetime.combine(start, datetime.min.time(), tzinfo=TZ), "week", TZ)
    async with sessions() as session:
        check = await create_check(
            session,
            title="Run?",
            schedule="3 times a week from in two weeks",
            schedule_rule={"kind": "quota", "period": "week", "count": 3, "start": start.isoformat()},
        )
        assert check.period_start == first_week
        answered, after = await resolve_check(session, check.id, CheckOutcome.PASSED)
        assert answered.period_start == first_week and after.period_start == first_week
