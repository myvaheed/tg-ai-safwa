"""The real Schedule compiler and Advisor read-tool boundary, with a scripted model."""

from __future__ import annotations

import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import PROPOSALS, REGISTRY
from safwa.features.checks.model import Check
from safwa.features.checks.use_cases import create_check, resolve_check
from safwa.features.onboarding.hooks import ONBOARDING_HOOK
from safwa.features.profile.api import set_hook_switch
from safwa.features.schedules.agent import ScheduleCompiler
from safwa.features.schedules.hooks import SCHEDULE_CLARIFICATION_HOOK, compile_revision, recover
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.runtime import CueRuntime
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


async def test_advisor_reads_a_compiled_quota_and_its_missed_observation(e2e_harness):
    """SCH-CHECK-003 — tests/brd/schedules.feature"""
    async with e2e_harness.sessions() as session:
        check = await create_check(session, title="Posture?", schedule="five times a day")
        revision, check_id = check.schedule_id, check.id
        day = check.schedule_record.submitted_at.astimezone(ZoneInfo("Europe/Istanbul")).date()
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            CompletionTurn(
                content="",
                tool_calls=[
                    ToolCall(
                        id="compile",
                        name="set_schedule_config",
                        arguments_json='{"period":"day","count":5}',
                    )
                ],
            ),
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
    await compile_revision(
        revision,
        SimpleNamespace(
            sessions=e2e_harness.sessions,
            resources=SimpleNamespace(schedule_compiler=ScheduleCompiler(provider)),
        ),
    )
    async with e2e_harness.sessions() as session:
        _, successor = await resolve_check(session, check_id, "missed")
        assert successor is not None
        await session.commit()
    outcome = await advisor.handle("How many posture observations are planned and answered today?")
    report = json.loads(provider.calls[-1][-1]["content"])
    item = report["items"][0]
    assert item["range"] == {
        "planned": 5,
        "done": 1,
        "remaining": 4,
        "partial": True,
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


async def test_committed_schedule_is_clarified_by_advisor_and_saved_as_a_ready_plan(e2e_harness):
    """SCH-CLARIFY-005 — tests/brd/schedules.feature"""
    compiled = []

    async def complete(request):
        compiled.append(request.messages[-1]["content"])
        unclear = len(compiled) == 1
        return CompletionTurn(
            content="",
            tool_calls=[
                ToolCall(
                    id=str(len(compiled)),
                    name="not_clear_enough" if unclear else "set_schedule_config",
                    arguments_json='{"reason":"How many times per week?"}'
                    if unclear
                    else '{"period":"week","count":1}',
                )
            ],
        )

    resources = SimpleNamespace(
        schedule_compiler=ScheduleCompiler(SimpleNamespace(complete=complete))
    )
    context = SimpleNamespace(sessions=e2e_harness.sessions, resources=resources)
    sink = bind_committed(e2e_harness.sessions, REGISTRY.hooks, resources=resources)
    try:
        async with e2e_harness.sessions() as session:
            await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
            check = await create_check(session, title="Workout?", schedule="often each week")
            check_id = check.id
            await session.commit()
        await sink.drain()
        async with e2e_harness.sessions() as session:
            assert (
                await session.get(Check, check_id)
            ).schedule_record.status == "needs_clarification"
            assert list(await session.scalars(select(Cue.hook))) == [
                SCHEDULE_CLARIFICATION_HOOK.name
            ]
        await recover(None, context)
        assert len(compiled) == 1

        advisor, provider = e2e_harness.advisor(
            [
                "How many times per week do you want to answer Workout?",
                route_turn("workspace_mutator"),
                mutation_turn(
                    ("check", {"mode": "update", "id": check_id, "schedule": "once each week"})
                ),
                "Updated Check Schedule.",
                "Scheduled once each week.",
            ]
        )
        delivered = []

        async def speak(event_id, text, shown=()):
            delivered.append(text)
            assert "How many times per week" in (await advisor.handle(text)).message
            return True

        async def gate():
            return True

        runtime = CueRuntime(
            SimpleNamespace(sessions=e2e_harness.sessions, hooks=REGISTRY.hooks),
            bot=None,
            owner_id=42,
        )
        assert await tick(
            e2e_harness.sessions,
            gate=gate,
            speak=speak,
            delivered=runtime.delivered,
            prepare=runtime.prepare,
        )
        assert len(delivered) == 1
        assert f"check #{check_id}" in delivered[0] and "often each week" in delivered[0]
        assert "route workspace_mutator" in delivered[0]
        outcome = await advisor.handle("Once each week.")
        assert outcome.proposal_id is not None
        async with e2e_harness.sessions() as session:
            affected = await approve_proposal(
                session, advisor.reviews, PROPOSALS, outcome.proposal_id
            )
            await session.commit()
        await sink.drain()
        async with e2e_harness.sessions() as session:
            check = await session.get(Check, check_id)
            assert check.schedule == "once each week"
            assert check.schedule_record.status == "ready"
            assert check.schedule_record.rule == {"kind": "quota", "period": "week", "count": 1}
            assert check.period_start is not None
            assert list(await session.scalars(select(Cue))) == []
        assert len(compiled) == 2 and "once each week" in compiled[1]
        # Saving a proposal resumes the suspended root with its actual approval receipt.
        resumed = await advisor.resolve_approval(
            outcome.proposal_id,
            decision=BatchDecision.APPROVED,
            result={"affected_ids": affected},
        )
        assert resumed is not None and "Scheduled once each week." in resumed.message
        assert not provider.responses
    finally:
        await sink.close()
