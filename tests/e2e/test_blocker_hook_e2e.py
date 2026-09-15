"""A blocker saved through a proposal becomes one request the Advisor is handed, worded late.

The real registry, the real commit listener and the real Cue poll; the provider is
scripted, and the Advisor turn is replaced by a recorder so the words can be read.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS, REGISTRY
from safwa.features.cards.hooks import BLOCKER_HOOK
from safwa.features.cards.use_cases import create_card, update_card_fields
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.runtime import CueRuntime
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e

OWNER_ID = 42


async def _pending(harness) -> list[tuple[str | None, list | None]]:
    async with harness.sessions() as session:
        return [(cue.hook, cue.payload) for cue in await session.scalars(select(Cue))]


async def _open_gate() -> bool:
    return True


async def test_cd_blocked_034_a_blocker_saved_by_proposal_or_by_hand_is_one_request(e2e_harness):
    """CD-BLOCKED-034 — tests/brd/cards.feature"""
    sink = bind_committed(e2e_harness.sessions, REGISTRY.hooks)
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Call the bank")
        await session.commit()
        card_id = card.id

    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("card", {
                "mode": "update", "id": card_id,
                "blocked": True, "blocked_description": "Line is busy",
            })),
            "Marked as blocked.",
        ]
    )
    outcome = await advisor.handle("The bank does not answer")
    assert outcome.proposal_id is not None
    # Prepared is not saved: a proposal the owner has not approved asks nothing.
    await sink.drain()
    assert await _pending(e2e_harness) == []

    async with e2e_harness.sessions() as session:
        await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
    await sink.drain()
    assert await _pending(e2e_harness) == [(BLOCKER_HOOK.name, [card_id])]

    # A second one by hand joins the same request; the first is unblocked before it is said.
    async with e2e_harness.sessions() as session:
        second = await create_card(
            session, kind="action", title="Sign the lease", effort_points=1,
            blocked=True, blocked_description="Landlord away",
        )
        await session.commit()
        second_id = second.id
    async with e2e_harness.sessions() as session:
        await update_card_fields(session, card_id, {"blocked": False})
        await session.commit()
    await sink.drain()
    assert await _pending(e2e_harness) == [(BLOCKER_HOOK.name, [card_id, second_id])]

    said: list[str] = []

    async def speak(event_id: str, text: str) -> bool:
        said.append(text)
        return True

    runtime = CueRuntime(
        SimpleNamespace(sessions=e2e_harness.sessions, hooks=REGISTRY.hooks),  # type: ignore[arg-type]
        bot=None,  # type: ignore[arg-type]
        owner_id=OWNER_ID,
    )
    assert await tick(
        e2e_harness.sessions, gate=_open_gate, speak=speak, prepare=runtime.prepare
    ) is True
    assert len(said) == 1
    assert f"#{second_id} «Sign the lease»: Landlord away" in said[0]
    assert "Call the bank" not in said[0]
    assert await _pending(e2e_harness) == []
