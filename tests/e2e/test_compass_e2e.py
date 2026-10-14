"""The look at the whole tree in a real Advisor turn: the Morning's findings handed to Compass,
and its words said as they are.

The real registry, the real Cue row, the real Compass bound from its own declaration; only
the provider is scripted.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import route_turn
from agent_turns import forward_turn
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import REGISTRY
from safwa.features.cards.use_cases import create_card
from safwa.features.compass.graph_review import (
    COMPASS_REVIEW_DAYS,
    GRAPH_REVIEW_HOOK,
    GraphReviewer,
)
from safwa.features.profile.api import morning_time
from safwa.features.values.use_cases import create_value
from safwa.foundation.workspace import require_workspace
from telegram_llm import DialogueMessage
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.cues.initiatives import queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.hooks.contracts import RunContext, Tick

pytestmark = pytest.mark.e2e


class ClearProvider:
    async def complete(self, request):
        return CompletionTurn(
            content="", tool_calls=(ToolCall(id="graph", name="clear", arguments_json="{}"),)
        )


async def test_cmp_review_003_the_morning_findings_are_said_in_compass_own_words(e2e_harness):
    """CMP-REVIEW-003 — tests/brd/compass.feature"""
    sessions = e2e_harness.sessions
    async with sessions() as session:
        workspace = await require_workspace(session)
        workspace.created_at = utcnow() - timedelta(days=COMPASS_REVIEW_DAYS)
        health = await create_value(session, "Health", "Sleep and move, so I have energy for my kids")
        goal = await create_card(session, kind="goal", title="Earn $20k")
        await session.commit()
        ids = SimpleNamespace(health=health.id, goal=goal.id)

    async def nobody_publishes(text, kind):
        raise AssertionError("a hook on a tick has no chat")

    work = RunContext(
        resources=SimpleNamespace(graph_reviewer=GraphReviewer(ClearProvider())),
        still_current=lambda: True,
        publish=nobody_publishes,
        sessions=sessions,
    )
    await queue_advice(REGISTRY.hooks, sessions, Tick("08:00", clock=morning_time), work=work)
    async with sessions() as session:
        [cue] = [cue for cue in await session.scalars(select(Cue)) if cue.hook == GRAPH_REVIEW_HOOK.name]
    request = await REGISTRY.hooks.prepare(sessions, GRAPH_REVIEW_HOOK.name, cue.payload)
    assert f"[Earn $20k](card:{ids.goal}): a root Goal that serves no Value." in request
    assert f"[Health](value:{ids.health}): no open Goal, Action or Check carries it." in request

    answer = (
        "I looked at the whole tree again, and two things could be set better:\n"
        f"- [Earn $20k](card:{ids.goal}) serves no Value yet. What is it for?\n"
        f"- Nothing open carries [Health](value:{ids.health}). Shall we go through them together now?"
    )
    advisor, provider = e2e_harness.advisor(
        [route_turn("compass"), answer, forward_turn("compass")],
        subagents=(e2e_harness.subagent("workspace_mutator"), e2e_harness.subagent("compass")),
    )
    outcome = await advisor.handle(
        request, dialogue=[DialogueMessage(role="user", content=request)]
    )

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == answer
    # Compass reads the request and what SQL found, and is offered nothing that changes data.
    read = "\n".join(str(message["content"]) for message in provider.calls[1])
    assert request in read
    assert f"- [Earn $20k](card:{ids.goal}): a root Goal that serves no Value." in read
    offered = {tool["function"]["name"] for tool in provider.options[1]["tools"]}
    assert offered == {"query_data", "get_more_about"}
