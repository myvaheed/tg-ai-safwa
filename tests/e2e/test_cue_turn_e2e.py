"""A turn nobody asked for, and the owner arriving in the middle of it.

The real Advisor, the real review store and the real Cue poll; the provider is the only
thing replaced, and it is what holds the turn open at the point the owner writes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn
from sqlalchemy import func, select

from conftest import ScriptedProvider
from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.model import Card
from tg_agent_shell.ai.runs import AgentRun
from tg_agent_shell.cues.background import BLOCK_SHOWN, tick
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_cue, add_hook_cue
from tg_agent_shell.cues.runtime import CueRuntime
from tg_agent_shell.hooks.contracts import Advise, HookSpec, OnCommitted, Shown
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.proposals.use_cases import approve_proposal
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
        hooks=HookRegistry.of(),
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
            delivered=runtime.delivered,
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
        [
            route_turn("workspace_mutator"),
            mutation_turn(("card", {"mode": "update", "id": card_id, "title": "Buy oat milk"})),
        ],
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


class FailingProvider(ScriptedProvider):
    """A scripted provider whose script can say that a call fails, as a provider that is down."""

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        if self.responses and isinstance(self.responses[0], Exception):
            raise self.responses.popleft()
        return await super().complete(request)


async def test_ag_turn_015_a_follow_up_that_fails_after_a_save_leaves_nothing_open(
    e2e_harness,
):
    """AG-TURN-015 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Buy milk")
        await session.commit()
        card_id = card.id
    # The subagent answers after the Save and the Advisor's request after it fails: the
    # Advisor's claim used to stay taken, and the gate stayed shut until a restart.
    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("card", {"mode": "update", "id": card_id, "title": "Buy oat milk"})),
            "Renamed it.",
            ConnectionError("The provider is down"),
        ],
        provider_factory=FailingProvider,
    )
    proposal = await advisor.handle("Rename it to Buy oat milk")
    assert proposal.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(
            session, advisor.reviews, PROPOSALS, proposal.proposal_id
        )

    outcome = await advisor.resolve_approval(
        proposal.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    assert outcome is not None and "could not be generated" in outcome.message
    async with e2e_harness.sessions() as session:
        runs = {
            run.kind: (run.status, run.claimed_at)
            for run in await session.scalars(select(AgentRun))
        }
    assert runs == {"advisor": ("failed", None), "workspace_mutator": ("completed", None)}
    assert advisor.reviews.busy is False
    assert await _cue_runtime(e2e_harness, advisor, TurnManager()).can_speak() is True


async def _no_review_and_no_claim_is_left(harness, advisor) -> None:
    """Every review the turn opened ended with it, and so did the request behind it."""
    assert advisor.reviews.open_proposals == ()
    assert advisor.reviews.open_batches == ()
    async with harness.sessions() as session:
        runs = [
            (run.kind, run.status, run.claimed_at)
            for run in await session.scalars(select(AgentRun).order_by(AgentRun.id))
        ]
    assert runs == [
        ("advisor", "abandoned", None),
        ("workspace_mutator", "abandoned", None),
    ]
    assert await _cue_runtime(harness, advisor, TurnManager()).can_speak() is True


async def test_ag_turn_015_a_turn_that_fails_before_its_screen_is_drawn_leaves_nothing_open(
    e2e_harness, monkeypatch
):
    """AG-TURN-015 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Buy milk")
        await session.commit()
        card_id = card.id
    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("card", {"mode": "update", "id": card_id, "title": "Buy oat milk"})),
        ],
        autoapprove=True,
    )

    # Autoapproval reads the review before its screen is drawn, and that read fails.
    async def unreadable(_session, _proposal_id):
        raise RuntimeError("The review could not be read")

    monkeypatch.setattr(advisor.review_view, "describe", unreadable)

    with pytest.raises(RuntimeError):
        await advisor.handle("Rename it to Buy oat milk")

    await _no_review_and_no_claim_is_left(e2e_harness, advisor)


async def test_ag_turn_010_cancelled_while_the_next_screen_is_checked_leaves_nothing_open(
    e2e_harness,
):
    """AG-TURN-010 — tests/brd/tg_agent_shell/agents.feature"""
    first_card = await _card(e2e_harness, "Buy milk")
    second_card = await _card(e2e_harness, "Buy bread")
    reviews: list[CompletionRequest] = []

    def second_review(request: CompletionRequest) -> bool:
        if _autoapproval_call(request):
            reviews.append(request)
        return len(reviews) == 2

    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(
                ("card", {"mode": "update", "id": first_card, "title": "Buy oat milk"}),
                ("card", {"mode": "update", "id": second_card, "title": "Buy rye bread"}),
            ),
            CompletionTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="review-first",
                        name="require_review",
                        arguments_json='{"reason": "Keep the first one manual."}',
                    ),
                ),
            ),
        ],
        autoapprove=True,
        provider_factory=lambda responses: GatedProvider(responses, stop_on=second_review),
    )
    first = await advisor.handle("Rename both")
    assert first.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)

    # Saved, and the second screen is being checked when the owner runs /cancel.
    saving = asyncio.create_task(
        advisor.resolve_approval(
            first.proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": affected}
        )
    )
    await asyncio.wait_for(provider.reached.wait(), timeout=5)
    saving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await saving

    await _no_review_and_no_claim_is_left(e2e_harness, advisor)
    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, first_card)).title == "Buy oat milk"
        assert (await session.get(Card, second_card)).title == "Buy bread"


async def _card(harness, title: str) -> int:
    async with harness.sessions() as session:
        card = await create_manual_card(session, title=title)
        await session.commit()
        return card.id


async def _nothing(event) -> tuple:
    return ()


def _advising(name: str, words: Shown) -> HookSpec:
    async def prepare(session, items) -> Shown:
        return words

    return HookSpec(
        name=name, owner="test", on=(OnCommitted(kind=name),), evaluate=_nothing,
        effect=Advise(prepare=prepare), title=name, description=name,
    )


async def test_ag_hook_048_the_blocks_open_the_single_message_the_advisor_writes(
    e2e_harness, monkeypatch
):
    """AG-HOOK-048 — tests/brd/tg_agent_shell/agents.feature"""
    first = Shown(block="Still standing:\n\n- Walk\n- Walk", request="Ask which still matter.")
    last = Shown(block="Nothing else.", request="Ask about the rest.")
    async with e2e_harness.sessions() as session:
        await add_hook_cue(session, hook="first", items=[1])
        await add_hook_cue(session, hook="last", items=[2])
        await session.commit()
    advisor, provider = e2e_harness.advisor(["Which of these still matter?"])
    runtime = _cue_runtime(e2e_harness, advisor, TurnManager())
    runtime.services.hooks = HookRegistry.of(
        (_advising("first", first), _advising("last", last)), owners=frozenset({"test"})
    )
    said: list[str] = []

    async def render(_message, _services, outcome, *, kind, event_id) -> None:
        said.append(outcome.message)

    monkeypatch.setattr("tg_agent_shell.cues.runtime.render_ai_outcome", render)

    assert await tick(
        e2e_harness.sessions, gate=runtime.can_speak, speak=runtime.speak,
        delivered=runtime.delivered, release=runtime.release, prepare=runtime.prepare,
    )

    assert said == [
        "Still standing:\n\n- Walk\n- Walk\n\nNothing else.\n\nWhich of these still matter?"
    ]
    read = "\n".join(str(message["content"]) for message in provider.calls[0])
    assert f"Ask which still matter.\n{BLOCK_SHOWN}" in read
    assert f"Ask about the rest.\n{BLOCK_SHOWN}" in read
    assert "Still standing" not in read
