"""An Action finished without its time becomes one request, and the owner's answer is saved
on that Card.

The real registry, the real commit listener, the real Cue poll and a real proposal Save;
the provider is scripted, and the Advisor turn that says the request is a recorder.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS, REGISTRY
from safwa.features.cards.model import Card, minutes_label
from safwa.features.cards.use_cases import finish_action
from safwa.features.onboarding.hooks import ONBOARDING_HOOK
from safwa.features.profile.api import TIME_TRACKING_REMINDER, set_hook_switch
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
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


async def test_cd_time_041_an_action_done_by_hand_is_asked_about_and_the_answer_is_saved(
    e2e_harness,
):
    """CD-TIME-041 — tests/brd/cards.feature"""
    sink = bind_committed(e2e_harness.sessions, REGISTRY.hooks)
    # The tips would ask about the Card finished here; this is the time's request alone.
    async with e2e_harness.sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        card = await create_manual_card(session, title="Write the report")
        await session.commit()
        card_id = card.id

    # Off in a new workspace: finishing asks nothing.
    async with e2e_harness.sessions() as session:
        await finish_action(session, card_id)
        await session.commit()
    await sink.drain()
    assert await _pending(e2e_harness) == []

    async with e2e_harness.sessions() as session:
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        second = await create_manual_card(session, title="Read the contract")
        await session.commit()
        second_id = second.id
    async with e2e_harness.sessions() as session:
        await finish_action(session, second_id)
        await session.commit()
    await sink.drain()
    assert await _pending(e2e_harness) == [(TIME_TRACKING_REMINDER, [second_id])]

    said: list[str] = []

    async def speak(event_id: str, text: str, shown=(), passing=None) -> bool:
        said.append(text)
        return True

    runtime = CueRuntime(
        SimpleNamespace(sessions=e2e_harness.sessions, hooks=REGISTRY.hooks),  # type: ignore[arg-type]
        bot=None,  # type: ignore[arg-type]
        owner_id=OWNER_ID,
    )
    assert await tick(
        e2e_harness.sessions,
        gate=_open_gate,
        speak=speak,
        delivered=runtime.delivered,
        prepare=runtime.prepare,
    ) is True
    assert len(said) == 1
    assert f"#{second_id} «Read the contract»" in said[0]
    assert "EP" not in said[0]
    assert "Write the report" not in said[0]
    assert await _pending(e2e_harness) == []

    # The owner's answer is an ordinary turn, and the time lands on that Card.
    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("action", {"mode": "update", "id": second_id, "tracked_mins": 90})),
            "Recorded an hour and a half.",
        ]
    )
    outcome = await advisor.handle("An hour and a half")
    assert outcome.proposal_id is not None
    async with e2e_harness.sessions() as session:
        await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
    async with e2e_harness.sessions() as session:
        tracked = (await session.get(Card, second_id)).tracked_mins
    assert (tracked, minutes_label(tracked)) == (90, "1h 30m")
    # A time given raises no question of its own.
    await sink.drain()
    assert await _pending(e2e_harness) == []
