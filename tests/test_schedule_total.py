"""A repeating Schedule that stops after a total, and the request that says so."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from agent_turns import PLAN
from hook_helpers import changes_of

from llm_gateway import CompletionTurn, ToolCall
from safwa.enums import ActorType
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, finish_action, update_card_fields
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import create_check, resolve_check, update_check_fields
from safwa.features.schedules.agent import SCHEDULE_PARSER_PROMPT, ScheduleCompiler
from safwa.features.schedules.api import (
    SCHEDULE_ENDED,
    get_scheduled,
    remaining_occurrences,
    rule_summary,
    schedule_summary,
    set_schedule,
    total_count,
)
from safwa.features.schedules.hooks import SCHEDULE_END_HOOK, end_request
from safwa.foundation.marks import closed_repeat_refusal
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.hooks.contracts import Advise, OnCommitted

TZ = ZoneInfo("Europe/Istanbul")
QUOTA = {"kind": "quota", "period": "day", "count": 5}


def scripted(*arguments):
    """A provider whose Scheduler session answers set_schedule_config with each in turn."""
    requests = []

    async def complete(request):
        requests.append(request)
        return CompletionTurn(
            content=PLAN,
            tool_calls=[
                ToolCall(
                    id=str(len(requests)),
                    name="set_schedule_config",
                    arguments_json=json.dumps(arguments[len(requests) - 1]),
                )
            ],
        )

    return SimpleNamespace(complete=complete), requests


async def test_the_scheduler_reads_a_total_beside_the_repetition():
    """SCH-TOTAL-021 — tests/brd/schedules.feature"""
    assert "'five times a day, 50 times in total': period=day, count=5, limit=50." in (
        SCHEDULE_PARSER_PROMPT
    )
    provider, _ = scripted({"period": "day", "count": 5, "limit": 50})
    rule, question = await ScheduleCompiler(provider).compile(
        "five times a day, 50 times in total", datetime.now(UTC), TZ, "check"
    )
    assert question is None and rule == {**QUOTA, "limit": 50}
    assert rule_summary(rule, TZ) == "5 per day, 50 times in total."


async def test_a_total_on_a_one_time_schedule_goes_back_to_repair():
    """SCH-TOTAL-021 — tests/brd/schedules.feature"""
    once = {"first_day": "20.10.2099", "time": "15:00"}
    provider, requests = scripted({**once, "limit": 3}, once)
    rule, question = await ScheduleCompiler(provider).compile(
        "on 20 October 2099 at 15:00", datetime.now(UTC), TZ, "check"
    )
    repair = json.loads(requests[1].messages[-1]["content"])
    assert repair["retryable"] is True and "A total or a last day needs a Schedule that repeats" in repair["error"]
    assert question is None and "limit" not in rule


async def test_the_instance_that_reaches_the_total_opens_no_next_one(sessions):
    """SCH-TOTAL-021 — tests/brd/schedules.feature"""
    async with sessions() as session:
        live = await create_check(
            session,
            title="Stretch?",
            schedule="after completion, 3 times in total",
            schedule_rule={"kind": "after_completion", "limit": 3},
        )
        # Passed and Missed both count.
        for outcome in (CheckOutcome.PASSED, CheckOutcome.MISSED):
            _, live = await resolve_check(session, live.id, outcome)
            assert live is not None
        assert await schedule_summary(session, live) == (
            "Repeats after each completion, 3 times in total. 2/3 answered so far."
        )
        _, after = await resolve_check(session, live.id, CheckOutcome.PASSED)
        assert after is None

        action = await create_card(
            session,
            kind="action",
            title="Water",
            schedule="after completion, 2 times in total",
            schedule_rule={"kind": "after_completion", "limit": 2},
        )
        first = await finish_action(session, action.id)
        (successor,) = first.successor_ids
        assert (await finish_action(session, successor)).successor_ids == []


async def test_a_changed_schedule_counts_its_total_from_zero(sessions):
    """SCH-TOTAL-021 — tests/brd/schedules.feature"""
    rule = {"kind": "after_completion", "limit": 2}
    async with sessions() as session:
        live = await create_check(
            session, title="Stretch?", schedule="after completion, 2 times", schedule_rule=rule
        )
        _, live = await resolve_check(session, live.id, CheckOutcome.PASSED)
        await set_schedule(session, live, "after each one, 2 times in total", rule)
        _, live = await resolve_check(session, live.id, CheckOutcome.PASSED)
        assert live is not None  # 1 of the new revision's 2
        _, after = await resolve_check(session, live.id, CheckOutcome.PASSED)
        assert after is None


async def test_planning_counts_no_more_than_the_total_has_left(sessions):
    """SCH-TOTAL-021 — tests/brd/schedules.feature"""
    async with sessions() as session:
        card = await create_card(
            session,
            kind="action",
            title="Water",
            schedule="five times a day, 3 times in total",
            schedule_rule={**QUOTA, "limit": 3},
        )
        today = datetime.now(UTC).astimezone(TZ).date()
        assert await remaining_occurrences(session, card, today, today + timedelta(days=2)) == 3
        (successor,) = (await finish_action(session, card.id)).successor_ids
        assert await remaining_occurrences(
            session, await session.get(type(card), successor), today, today + timedelta(days=2)
        ) == 2

        check = await create_check(
            session,
            title="Posture?",
            schedule="five times a day, 3 times in total",
            schedule_rule={**QUOTA, "limit": 3},
        )
        await resolve_check(session, check.id, CheckOutcome.MISSED)
        (item,) = (await get_scheduled(session, today, today, "check"))["items"]
        assert item["total"]["remaining"] == 2


async def test_safwa_is_asked_to_say_a_schedule_ran_its_total(sessions):
    """SCH-TOTAL-022 — tests/brd/schedules.feature"""
    assert SCHEDULE_END_HOOK.on == (OnCommitted(kind=SCHEDULE_ENDED),)
    assert isinstance(SCHEDULE_END_HOOK.effect, Advise)
    # An Advise is in Profile → Hooks, under its title.
    assert SCHEDULE_END_HOOK.agent_related and SCHEDULE_END_HOOK.title == "Schedule end"
    rule = {"kind": "after_completion", "limit": 1}
    async with sessions() as session:
        check = await create_check(session, title="Stretch?", schedule="once more", schedule_rule=rule)
        action = await create_card(
            session, kind="action", title="Water", schedule="once more", schedule_rule=rule
        )
        await resolve_check(session, check.id, CheckOutcome.PASSED)
        await finish_action(session, action.id)
        assert changes_of(session, SCHEDULE_ENDED) == [
            Committed(SCHEDULE_ENDED, check.id, {"type": "check", "reason": "total"}),
            Committed(SCHEDULE_ENDED, action.id, {"type": "card", "reason": "total"}),
        ]
        await session.commit()

        request = await end_request(
            session, [["check", check.id, "total"], ["card", action.id, "total"]]
        )
        assert f"- Check #{check.id} «Stretch?», total 1" in request
        assert f"- Action #{action.id} «Water», total 1" in request
        assert "ask whether to start it again" in request
        assert "Change nothing until they answer." in request
        assert "propose its Schedule on that #id, in the same words or new ones" in request


async def test_a_schedule_on_the_last_instance_starts_the_series_again(sessions):
    """SCH-TOTAL-023 — tests/brd/schedules.feature"""
    rule = {"kind": "after_completion", "limit": 2}
    words = "after completion, 2 times in total"
    async with sessions() as session:
        action = await create_card(
            session, kind="action", title="Water", schedule=words, schedule_rule=rule
        )
        (second,) = (await finish_action(session, action.id)).successor_ids
        assert (await finish_action(session, second)).successor_ids == []
        last = await session.get(Card, second)
        ended_revision = last.schedule_id

        # The same words start it again on a next instance, counted from 0.
        again = await update_card_fields(
            session, second, {"schedule": words, "schedule_rule": rule}, actor=ActorType.AI
        )
        assert again.id != second and again.completed_at is None
        assert again.repeat_series_id == last.repeat_series_id
        assert again.schedule == words and again.schedule_id != ended_revision
        assert last.schedule_id == ended_revision and last.completed_at is not None
        assert await total_count(session, again) == 0
        (third,) = (await finish_action(session, again.id)).successor_ids
        assert (await finish_action(session, third)).successor_ids == []

        # An older instance of the series still refuses a Schedule.
        with pytest.raises(DomainError, match="current instance"):
            await update_card_fields(
                session, action.id, {"schedule": "after each one", "schedule_rule": rule}
            )

        check = await create_check(
            session, title="Stretch?", schedule="after completion, once", schedule_rule={**rule, "limit": 1}
        )
        _, after = await resolve_check(session, check.id, CheckOutcome.PASSED)
        assert after is None
        restarted = await update_check_fields(
            session, check.id, {"schedule": "after completion", "schedule_rule": {"kind": "after_completion"}}
        )
        assert restarted.id != check.id and restarted.outcome is None
        assert restarted.series_id == check.id == check.series_id
        assert check.schedule == "after completion, once"


async def test_a_proposal_may_start_an_ended_series_again(sessions):
    """SCH-TOTAL-023 — tests/brd/schedules.feature"""
    rule = {"kind": "after_completion", "limit": 2}
    async with sessions() as session:
        action = await create_card(
            session, kind="action", title="Water", schedule="after completion", schedule_rule=rule
        )
        (second,) = (await finish_action(session, action.id)).successor_ids
        first = await session.get(Card, action.id)
        live = await session.get(Card, second)
        # Its series is still open, so the closed instance takes no Schedule.
        assert await closed_repeat_refusal(session, first, "card", starts_again=True) is not None
        await finish_action(session, second)
        assert await closed_repeat_refusal(session, live, "card", starts_again=True) is None
        assert await closed_repeat_refusal(session, live, "card") is not None
