"""An analysed Sprint reaches the Advisor's memory: the real rows, the real poll body, the
real matcher on a scripted provider, and the Advisor's own context builder reading it back."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import ScriptedProvider
from llm_gateway import CompletionTurn, ToolCall
from safwa.features.cards.use_cases import create_card
from safwa.features.memory.absorb import PatternReviewer
from safwa.features.memory.use_cases import AbsorbResult, absorb_due
from safwa.features.planning.model import Sprint
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.retro.use_cases import record_analysis
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.turn import TurnManager

pytestmark = pytest.mark.e2e


def _analysis(helped: list[str], hurt: list[str], notable: list[str]) -> dict:
    return {
        "headline": "Спринт прошёл ровно.",
        "dynamics": [],
        "helped": helped,
        "hurt": hurt,
        "noise": [],
        "experiment": "Не больше двух встреч в день.",
        "notable": notable,
        "dropped": [],
        "compared": [],
    }


async def _analysed_sprint(harness, *, ended_days_ago: int, analysis: dict) -> str:
    async with harness.sessions() as session:
        await create_card(session, kind="action", title="Do", stage="sprint", effort_points=1)
        sprint = await start_sprint(session, success_criteria="Ship")
        sprint.actual_started_at = utcnow() - timedelta(days=ended_days_ago + 3)
        await finish_sprint(session)
        sprint.actual_ended_at = utcnow() - timedelta(days=ended_days_ago)
        await record_analysis(session, sprint.id, analysis)
        await session.commit()
        return sprint.number


def _review(same: list[tuple[int, int]]) -> CompletionTurn:
    arguments = {
        "verbose_analyse": "The walk is the same walk.",
        "same": [{"candidate": candidate, "pattern": pattern} for candidate, pattern in same],
    }
    return CompletionTurn("", tool_calls=(ToolCall("c1", "pattern_review", json.dumps(arguments)),))


async def test_mem_retro_011_an_analysed_sprint_reaches_the_advisor_on_its_own(e2e_harness):
    """MEM-RETRO-011 — tests/brd/memory.feature"""
    first = await _analysed_sprint(
        e2e_harness,
        ended_days_ago=20,
        analysis=_analysis(["утренняя прогулка"], ["три встречи подряд"], []),
    )
    second = await _analysed_sprint(
        e2e_harness,
        ended_days_ago=2,
        analysis=_analysis(["прогулка перед работой"], [], ["переезд"]),
    )
    provider = ScriptedProvider([_review([(1, 1)])])
    turn = TurnManager()

    async def poll() -> AbsorbResult:
        return await absorb_due(
            PatternReviewer(provider), e2e_harness.sessions, run_background=turn.run_background
        )

    # The first Sprint is taken in with no question to ask; the second is matched once.
    assert await poll() == AbsorbResult.ABSORBED
    assert provider.calls == []
    assert await poll() == AbsorbResult.ABSORBED
    assert len(provider.calls) == 1
    asked = provider.calls[0][1]["content"]
    assert "Patterns, numbered:\n1. утренняя прогулка\n" in asked
    assert "Candidates, numbered:\n1. прогулка перед работой" in asked
    assert await poll() == AbsorbResult.NOTHING
    async with e2e_harness.sessions() as session:
        rows = list(await session.scalars(select(Sprint)))
    assert all(row.memory_at is not None for row in rows)

    # The Advisor's next answer reads it, before the workspace state.
    advisor, advisor_provider = e2e_harness.advisor(["Noted."])
    await advisor.handle("Hi")
    context = advisor_provider.calls[0][-1]["content"]
    memory = context.split("Persistent memory:", 1)[1].split("Current workspace state:", 1)[0]
    assert "- утренняя прогулка (2 Sprints)" in memory
    assert "- три встречи подряд (1 Sprint)" in memory
    assert f"Last analysed Sprint {second}" in memory and first not in memory.split("Last analysed")[1]
    assert "Experiment it set, result not checked: Не больше двух встреч в день." in memory
    assert "Worth knowing: переезд" in memory
