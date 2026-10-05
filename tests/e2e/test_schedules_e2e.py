"""The real Schedule compiler and Advisor read-tool boundary, with a scripted model."""

from __future__ import annotations

import json
from zoneinfo import ZoneInfo

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn, schedule_turn
from sqlalchemy import func, select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.checks.model import Check
from safwa.features.checks.use_cases import create_check, resolve_check
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


async def test_advisor_reads_a_compiled_quota_and_its_missed_observation(e2e_harness):
    """SCH-CHECK-003 — tests/brd/schedules.feature"""
    async with e2e_harness.sessions() as session:
        check = await create_check(
            session,
            title="Posture?",
            schedule="five times a day",
            schedule_rule={"kind": "quota", "period": "day", "count": 5},
        )
        check_id = check.id
        day = check.schedule_record.submitted_at.astimezone(ZoneInfo("Europe/Istanbul")).date()
        _, successor = await resolve_check(session, check_id, "missed")
        assert successor is not None
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            CompletionTurn(
                content="",
                tool_calls=[
                    ToolCall(
                        id="scheduled",
                        name="get_scheduled",
                        arguments_json=json.dumps(
                            {
                                "start_date": day.isoformat(),
                                "end_date": day.isoformat(),
                                "type": "check",
                            }
                        ),
                    )
                ],
            ),
            "Five planned observations, one answered Missed, four remaining.",
        ]
    )
    outcome = await advisor.handle("How many posture observations are planned and answered today?")
    report = json.loads(provider.calls[-1][-1]["content"])
    item = report["items"][0]
    assert item["range"] == {
        "planned": 5,
        "done": 1,
        "remaining": 4,
        "passed": 0,
        "missed": 1,
    }
    assert item["next"]["remaining"] == 4
    assert item["total"] == {"done": 1, "remaining": None, "passed": 0, "missed": 1}
    assert item["id"] == successor.id
    assert "four remaining" in outcome.message
    assert not provider.responses


async def test_advisor_repairs_invalid_schedule_query_and_continues_the_turn(e2e_harness):
    """SCH-READ-011 — tests/brd/schedules.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            CompletionTurn(
                content="",
                tool_calls=[
                    ToolCall(
                        id="bad-date",
                        name="get_scheduled",
                        arguments_json='{"start_date":"03.10.2026","end_date":"03.10.2026","type":"card"}',
                    )
                ],
            ),
            CompletionTurn(
                content="",
                tool_calls=[
                    ToolCall(
                        id="repaired-date",
                        name="get_scheduled",
                        arguments_json='{"start_date":"2026-10-03","end_date":"2026-10-03","type":"card"}',
                    )
                ],
            ),
            "No Actions are scheduled on that date.",
        ]
    )
    outcome = await advisor.handle("What is scheduled for October 3?")
    assert json.loads(provider.calls[1][-1]["content"])["retryable"] is True
    assert json.loads(provider.calls[2][-1]["content"])["items"] == []
    assert outcome.message == "No Actions are scheduled on that date."
    assert not provider.responses


async def test_an_unclear_schedule_is_asked_in_the_same_reply_and_saved_ready(e2e_harness):
    """SCH-CLARIFY-005 — tests/brd/schedules.feature"""
    question = "How many times a week?"
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("check", {"mode": "create", "title": "Workout?", "schedule": "often"})),
            "Proposed the Check.",
            schedule_turn("not_clear_enough", reason=question),
            f"The Scheduler asks: {question}",
            question,
        ]
    )
    outcome = await advisor.handle("Add a Check Workout? that I answer often")
    assert outcome.proposal_id is None and outcome.message == question
    refusal = json.loads(provider.calls[4][-1]["content"])
    assert (refusal["code"], refusal["error"]) == ("schedule_unclear", question)
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Check.id))) == 0
        assert list(await session.scalars(select(Cue))) == []
    assert not provider.responses

    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(
                ("check", {"mode": "create", "title": "Workout?", "schedule": "once a week"})
            ),
            "Proposed the Check.",
            schedule_turn(period="week", count=1),
        ]
    )
    outcome = await advisor.handle("Once a week.")
    assert outcome.proposal_id is not None
    async with e2e_harness.sessions() as session:
        [check_id] = await approve_proposal(
            session, advisor.reviews, PROPOSALS, outcome.proposal_id
        )
        await session.commit()
    async with e2e_harness.sessions() as session:
        check = await session.get(Check, check_id)
        assert check.schedule_record.rule == {"kind": "quota", "period": "week", "count": 1}
        assert check.period_start is not None
    assert not provider.responses
