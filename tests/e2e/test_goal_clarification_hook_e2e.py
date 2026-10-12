from __future__ import annotations

from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.goal_review import GOAL_CLARIFICATION_HOOK, GoalReviewer
from safwa.features.cards.model import Card
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


async def test_saved_mutator_goal_is_reviewed_and_its_note_written_asks_nothing_more(e2e_harness):
    """CD-GOAL-051 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    calls = []

    async def complete(request):
        calls.append(request)
        return mutation_turn(("unclear", {"advice": "Done is not observable. Ask what result shows it."}))

    registry = HookRegistry.of((GOAL_CLARIFICATION_HOOK,), owners=frozenset({"cards"}))
    sink = bind_committed(sessions, registry, resources=SimpleNamespace(
        goal_reviewer=GoalReviewer(SimpleNamespace(complete=complete))
    ))
    try:
        advisor, _ = e2e_harness.advisor([
            route_turn("workspace_mutator"),
            mutation_turn(("goal", {"mode": "create", "title": "Learn Spanish"})),
            "Proposed the Goal.",
        ])
        outcome = await advisor.handle("Add a Goal: learn Spanish")
        assert outcome.proposal_id is not None
        await sink.drain()
        assert calls == []
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
            goal_id = await session.scalar(select(Card.id).where(Card.title == "Learn Spanish"))
        await sink.drain()
        async with sessions() as session:
            cues = list(await session.scalars(select(Cue)))
        assert len(calls) == 1
        assert [cue.hook for cue in cues] == [GOAL_CLARIFICATION_HOOK.name]

        reply = "How will you know «Learn Spanish» is Done? Passing DELE B2, perhaps?"
        advisor, _ = e2e_harness.advisor([
            mutation_turn(("query_data", {
                "sql": f"SELECT id, title, note FROM ai_cards WHERE id={goal_id}"
            }), content=""),
            reply,
        ])
        said = []

        async def speak(event_id, text, shown=(), passing=None):
            assert f"#{goal_id} «Learn Spanish»" in text
            said.append((await advisor.handle(text)).message)
            return True

        async def free():
            return True

        async def delivered(event_id):
            return False

        async def prepare(hook, items):
            return await registry.prepare(sessions, hook, items)

        assert await tick(sessions, gate=free, speak=speak, delivered=delivered, prepare=prepare)
        assert said == [reply]
        async with sessions() as session:
            assert list(await session.scalars(select(Cue))) == []

        advisor, _ = e2e_harness.advisor([
            route_turn("workspace_mutator"),
            mutation_turn(("goal", {"mode": "update", "id": goal_id, "note": "Pass DELE B2"})),
            "Proposed the criterion.",
        ])
        outcome = await advisor.handle("Yes, DELE B2")
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
            assert (await session.get(Card, goal_id)).note == "Pass DELE B2"
        await sink.drain()
        assert len(calls) == 1
    finally:
        await sink.close()
