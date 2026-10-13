from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn
from sqlalchemy import select
from ui_harness import history_source

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.action_review import ACTION_REVIEW_HOOK, ActionReviewer
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import finish_action
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import register_message
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e

OWNER = 42
ADVICE = "1 EP looks low: the finished Run 15 km took 5. Suggest 5."


async def test_saved_action_is_reviewed_with_the_chat_and_a_similar_finished_one(e2e_harness):
    """CD-ACTION-052 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    calls = []

    async def complete(request):
        calls.append(request)
        return mutation_turn(("unclear", {"advice": ADVICE}))

    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        done = await create_manual_card(session, title="Run 15 km", effort_points=5)
        await finish_action(session, done.id)
        for number in range(1, 7):
            await register_message(
                session, OWNER, 2 * number, "in", MessageKind.DIALOGUE_USER,
                text=f"message {number}",
            )
            await register_message(
                session, OWNER, 2 * number + 1, "out", MessageKind.DIALOGUE_ASSISTANT,
                text=f"answer {number}",
            )
        await session.commit()

    registry = HookRegistry.of((ACTION_REVIEW_HOOK,), owners=frozenset({"cards"}))
    sink = bind_committed(sessions, registry, resources=SimpleNamespace(
        action_reviewer=ActionReviewer(
            SimpleNamespace(complete=complete), history_source(sessions), OWNER,
            e2e_harness.search,
        )
    ))
    try:
        advisor, _ = e2e_harness.advisor([
            route_turn("workspace_mutator"),
            mutation_turn(("action", {
                "mode": "create", "title": "Run 20 km", "effort_points": 1,
                "categories": ["rest"], "energy_types": ["physical"],
            })),
            "Proposed the Action.",
        ])
        outcome = await advisor.handle("Add an Action: run 20 km")
        assert outcome.proposal_id is not None
        await sink.drain()
        assert calls == []
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
            action_id = await session.scalar(select(Card.id).where(Card.title == "Run 20 km"))
        await sink.drain()
        assert len(calls) == 1
        context = json.loads(calls[0].messages[1]["content"])
        assert context["effort_points"] == 1
        assert context["categories"] == ["rest"] and context["energy_types"] == ["physical"]
        assert context["similar"] == [{"title": "Run 15 km", "effort_points": 5, "finished": True}]
        assert "message 6" in context["conversation"] and "answer 2" in context["conversation"]
        assert "message 1" not in context["conversation"]
        async with sessions() as session:
            cues = list(await session.scalars(select(Cue)))
        assert [cue.hook for cue in cues] == [ACTION_REVIEW_HOOK.name]

        reply = "«Run 20 km» could be improved: a finished Run 15 km took 5 EP. Set 5?"
        advisor, _ = e2e_harness.advisor([
            mutation_turn(("query_data", {
                "sql": f"SELECT id, title, effort_points FROM ai_cards WHERE id={action_id}"
            }), content=""),
            reply,
        ])
        said = []

        async def speak(event_id, text, shown=(), passing=None):
            assert f"#{action_id} «Run 20 km»: {ADVICE}" in text
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
            mutation_turn(("action", {"mode": "update", "id": action_id, "effort_points": 5})),
            "Proposed 5 EP.",
        ])
        outcome = await advisor.handle("Yes, 5")
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
            assert (await session.get(Card, action_id)).effort_points == 5
        await sink.drain()
        assert len(calls) == 1
    finally:
        await sink.close()
