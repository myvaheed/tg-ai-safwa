from __future__ import annotations

from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from schedule_helpers import create_card
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.model import Card
from safwa.features.values.hooks import VALUE_UNLINK_HOOK
from safwa.features.values.link_review import ValueLinkReviewer
from safwa.features.values.use_cases import create_value
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


async def test_saved_unlink_leads_to_a_proposed_note_without_the_value(e2e_harness):
    """VL-UNLINK-021 — tests/brd/values.feature"""
    sessions = e2e_harness.sessions
    calls = []

    async def complete(request):
        calls.append(request)
        return mutation_turn(("value_mention_review", {"mentioned": True}))

    registry = HookRegistry.of((VALUE_UNLINK_HOOK,), owners=frozenset({"values"}))
    sink = bind_committed(sessions, registry, resources=SimpleNamespace(
        value_link_reviewer=ValueLinkReviewer(SimpleNamespace(complete=complete))
    ))
    try:
        async with sessions() as session:
            value = await create_value(session, "Health")
            action = await create_card(
                session, kind="action", title="Run 5 km",
                note="Route along the river. Keeps my health up.", value_ids={value.id},
            )
            await session.commit()
            action_id, value_id = action.id, value.id
        advisor, _ = e2e_harness.advisor([
            route_turn("workspace_mutator"),
            mutation_turn(("action", {"mode": "unlink", "id": action_id, "values": value_id})),
            "Proposed the unlink.",
        ])
        outcome = await advisor.handle("Take Health off the run")
        await sink.drain()
        assert calls == []
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
        await sink.drain()
        async with sessions() as session:
            cues = list(await session.scalars(select(Cue)))
        assert len(calls) == 1
        assert [cue.hook for cue in cues] == [VALUE_UNLINK_HOOK.name]

        advisor, _ = e2e_harness.advisor([
            route_turn("workspace_mutator"),
            mutation_turn(("action", {
                "mode": "update", "id": action_id, "note": "Route along the river.",
            })),
            "Proposed the Note without Health.",
        ])
        proposals = []

        async def speak(event_id, text, shown=(), passing=None):
            assert f"(card:{action_id}): Value «Health»" in text
            proposals.append((await advisor.handle(text)).proposal_id)
            return True

        async def free():
            return True

        async def delivered(event_id):
            return False

        async def prepare(hook, items):
            return await registry.prepare(sessions, hook, items)

        assert await tick(sessions, gate=free, speak=speak, delivered=delivered, prepare=prepare)
        [proposal_id] = proposals
        assert proposal_id is not None
        async with sessions() as session:
            assert (await session.get(Card, action_id)).note.endswith("Keeps my health up.")
            await approve_proposal(session, advisor.reviews, PROPOSALS, proposal_id)
            await session.commit()
            assert (await session.get(Card, action_id)).note == "Route along the river."
            assert list(await session.scalars(select(Cue))) == []
        await sink.drain()
        assert len(calls) == 1
    finally:
        await sink.close()
