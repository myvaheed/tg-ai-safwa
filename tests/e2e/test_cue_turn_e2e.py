"""A turn nobody asked for, and the owner arriving in the middle of it.

The real Advisor, the real review store and the real Cue poll; the provider is the only
thing replaced, and it is what holds the turn open at the point the owner writes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn
from sqlalchemy import func, select

from conftest import ScriptedProvider
from llm_gateway import CompletionRequest, CompletionTurn
from safwa.features.cards.model import Card
from tg_agent_shell.ai.runs import AgentRun
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_cue
from tg_agent_shell.cues.runtime import CueRuntime
from tg_agent_shell.turn import TurnManager

pytestmark = pytest.mark.e2e

OWNER_ID = 42
CUE_TEXT = "Sprint 1 is over."


class GatedProvider(ScriptedProvider):
    """A scripted provider that holds one call open until the test lets it through.

    A provider call is the only part of a turn that takes real time, so it is where the
    owner arrives. The gate closes once, on the first call the predicate names.
    """

    def __init__(self, responses: list[str | CompletionTurn], *, stop_on) -> None:
        super().__init__(responses)
        self.stop_on = stop_on
        self.reached = asyncio.Event()
        self.go = asyncio.Event()

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        if not self.reached.is_set() and self.stop_on(request):
            self.reached.set()
            await self.go.wait()
        return await super().complete(request)


def _any_call(request: CompletionRequest) -> bool:
    return True


def _autoapproval_call(request: CompletionRequest) -> bool:
    """The reviewer's own call: it is offered the two words it may end with, and nothing else."""
    return any(tool["function"]["name"] == "autoapprove" for tool in request.tools)


async def _no_dialogue(owner_id: int) -> list:
    return []


def _cue_runtime(harness, advisor, turn: TurnManager) -> CueRuntime:
    """The real gate over the real turn and the real store; only the chat is absent."""
    services = SimpleNamespace(
        sessions=harness.sessions,
        turn=turn,
        root=advisor,
        history=SimpleNamespace(dialogue=_no_dialogue),
    )
    return CueRuntime(services, bot=None, owner_id=OWNER_ID)  # type: ignore[arg-type]


async def _queue_cue(harness) -> None:
    async with harness.sessions() as session:
        await add_cue(session, text=CUE_TEXT)
        await session.commit()


async def _interrupted_tick(harness, advisor, provider, turn: TurnManager) -> int:
    """One poll, stopped by the owner writing while the turn is at the provider.

    Returns how many reviews that turn had already opened when they wrote, which is what
    says which half of the race this run reproduced.
    """
    runtime = _cue_runtime(harness, advisor, turn)
    ticking = asyncio.create_task(
        tick(
            harness.sessions,
            gate=runtime.can_speak,
            speak=runtime.speak,
            release=runtime.release,
        )
    )
    await asyncio.wait_for(provider.reached.wait(), timeout=5)
    open_when_they_wrote = len(advisor.reviews.open_proposals)
    turn.cancel()
    assert await asyncio.wait_for(ticking, timeout=5) is False
    return open_when_they_wrote


async def _nothing_is_left_open(harness, advisor, turn: TurnManager) -> None:
    assert advisor.reviews.open_proposals == ()
    assert advisor.reviews.open_batches == ()
    async with harness.sessions() as session:
        claimed = await session.scalar(
            select(func.count(AgentRun.id)).where(AgentRun.claimed_at.is_not(None))
        )
        assert claimed == 0
        assert [cue.text for cue in await session.scalars(select(Cue))] == [CUE_TEXT]
    # Which is the whole point: the gate opens again, so the words are said on a later
    # tick rather than never for the life of the process.
    assert await _cue_runtime(harness, advisor, turn).can_speak() is True


async def test_ag_turn_015_the_owner_arriving_while_a_cue_is_prepared_leaves_it_owed(
    e2e_harness,
):
    """AG-TURN-015 — tests/brd/tg_agent_shell/agents.feature"""
    await _queue_cue(e2e_harness)
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Buy milk")
        await session.commit()
        card_id = card.id
    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("card", {"mode": "update", "id": card_id, "title": "Buy oat milk"}))],
        provider_factory=lambda responses: GatedProvider(responses, stop_on=_any_call),
    )
    turn = TurnManager()

    assert await _interrupted_tick(e2e_harness, advisor, provider, turn) == 0

    await _nothing_is_left_open(e2e_harness, advisor, turn)
    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).title == "Buy milk"


async def test_ag_cue_029_a_cue_turn_cancelled_before_autoapproval_leaves_no_review_behind(
    e2e_harness,
):
    """AG-CUE-029 — tests/brd/tg_agent_shell/agents.feature"""
    await _queue_cue(e2e_harness)
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Buy milk")
        await session.commit()
        card_id = card.id
    # The review is open by the time autoapproval is asked about it, so cancelling here is
    # what used to leave a screen nobody could see and a session nothing could answer.
    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("card", {"mode": "update", "id": card_id, "title": "Buy oat milk"}))],
        autoapprove=True,
        provider_factory=lambda responses: GatedProvider(
            responses, stop_on=_autoapproval_call
        ),
    )
    turn = TurnManager()

    # The review was open when they wrote, and it is gone now.
    assert await _interrupted_tick(e2e_harness, advisor, provider, turn) == 1

    await _nothing_is_left_open(e2e_harness, advisor, turn)
    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).title == "Buy milk"
