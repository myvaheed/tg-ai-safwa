"""The Cue queue: the row outlives the turn until the owner has the words."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from safwa.bootstrap.modules import REGISTRY
from safwa.features.cards.use_cases import create_card
from safwa.features.planning.api import SPRINT_ENDED
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from tg_agent_shell.ai.runs import AgentRun
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_cue, waiting_cues
from tg_agent_shell.cues.runtime import CueRuntime
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.proposals.model import ChangeAction, ProposalChange
from tg_agent_shell.proposals.store import PROPOSAL_REVIEW_MINUTES, ProposalStore
from tg_agent_shell.turn import TurnManager


class Recorder:
    """Stands in for the background lease and the Advisor turn."""

    def __init__(self, *, open_gate=True, delivered=True):
        self.open_gate = open_gate
        self.delivered = delivered
        self.said: list[str] = []
        self.events: list[str] = []
        self.releases = 0

    async def gate(self) -> bool:
        return self.open_gate

    async def speak(self, event_id: str, text: str) -> bool:
        self.events.append(event_id)
        self.said.append(text)
        return self.delivered

    def release(self) -> None:
        self.releases += 1


def _hooks(recorder: Recorder) -> dict:
    return {"gate": recorder.gate, "speak": recorder.speak, "release": recorder.release}


def _runtime(sessions, turn: TurnManager, reviews: ProposalStore) -> CueRuntime:
    """The real gate, over the real turn and the real store; only the bot is absent."""
    services = SimpleNamespace(
        sessions=sessions, turn=turn, root=SimpleNamespace(reviews=reviews)
    )
    return CueRuntime(services, bot=None, owner_id=1)  # type: ignore[arg-type]


async def test_ag_turn_015_the_gate_is_shut_while_anything_of_the_owners_is_open(sessions):
    """AG-TURN-015 — tests/brd/tg_agent_shell/agents.feature"""
    turn, reviews = TurnManager(), ProposalStore()

    turn.begin(7)
    assert await _runtime(sessions, turn, reviews).can_speak() is False
    turn.end(7)

    reviews.open_proposal(
        message="Готово?",
        workspace_revision=1,
        changes=[
            ProposalChange(entity="card", action=ChangeAction.CREATE, values={"title": "Рынок"})
        ],
    )
    assert await _runtime(sessions, turn, reviews).can_speak() is False
    reviews.end_proposal(reviews.open_proposals[0].id)

    async with sessions() as session:
        session.add(
            AgentRun(provider="test", model="test", status="running", claimed_at=utcnow())
        )
        await session.commit()
    assert await _runtime(sessions, turn, reviews).can_speak() is False


async def test_ag_turn_015_an_open_gate_takes_the_background_lease(sessions):
    """AG-TURN-015 — tests/brd/tg_agent_shell/agents.feature"""
    turn = TurnManager()
    runtime = _runtime(sessions, turn, ProposalStore())

    assert await runtime.can_speak() is True

    # The lease is the turn itself, so the owner arriving next still wins the chat.
    assert turn.background is True
    assert runtime.still_current() is True
    turn.cancel()
    assert runtime.still_current() is False


async def write(sessions, text: str) -> int:
    async with sessions() as session:
        cue = await add_cue(session, text=text)
        await session.commit()
        return cue.id


async def remaining(sessions) -> list[str]:
    async with sessions() as session:
        return [cue.text for cue in await session.scalars(select(Cue).order_by(Cue.id))]


async def test_ag_cue_029_a_waiting_cue_is_said_once_and_then_gone(sessions):
    """AG-CUE-029 — tests/brd/tg_agent_shell/agents.feature"""
    await write(sessions, "Sprint 1 is over.")
    recorder = Recorder()

    assert await tick(sessions, **_hooks(recorder)) is True

    assert recorder.said == ["Sprint 1 is over."]
    assert len(recorder.events[0]) == 32
    assert await remaining(sessions) == []
    assert await tick(sessions, **_hooks(recorder)) is False


async def test_ag_cue_029_a_closed_gate_leaves_the_cue_waiting(sessions):
    """AG-CUE-029 — tests/brd/tg_agent_shell/agents.feature"""
    await write(sessions, "Sprint 1 is over.")
    recorder = Recorder(open_gate=False)

    assert await tick(sessions, **_hooks(recorder)) is False

    assert not recorder.said
    assert await remaining(sessions) == ["Sprint 1 is over."]
    # No lease was taken, so none is given back.
    assert recorder.releases == 0


async def test_ag_cue_029_a_turn_that_did_not_land_leaves_the_cue_waiting(sessions):
    """AG-CUE-029 — tests/brd/tg_agent_shell/agents.feature"""
    await write(sessions, "Sprint 1 is over.")
    failing = Recorder(delivered=False)

    assert await tick(sessions, **_hooks(failing)) is False

    assert failing.said  # it was attempted
    assert await remaining(sessions) == ["Sprint 1 is over."]
    assert failing.releases == 1

    recorder = Recorder()
    assert await tick(sessions, **_hooks(recorder)) is True
    assert recorder.said == ["Sprint 1 is over."]


async def test_ag_cue_029_one_tick_says_everything_waiting_as_one_request_oldest_first(sessions):
    """AG-CUE-029 — tests/brd/tg_agent_shell/agents.feature"""
    await write(sessions, "Sprint 1 is over.")
    await write(sessions, "Sprint 2 is over.")
    async with sessions() as session:
        first = (await waiting_cues(session))[0].event_id
    failing = Recorder(delivered=False)

    # A turn that did not land leaves both owed, and nothing about either is thrown away.
    assert await tick(sessions, **_hooks(failing)) is False
    assert failing.said == ["Sprint 1 is over.\n\nSprint 2 is over."]
    assert await remaining(sessions) == ["Sprint 1 is over.", "Sprint 2 is over."]

    recorder = Recorder()
    assert await tick(sessions, **_hooks(recorder)) is True

    assert recorder.said == ["Sprint 1 is over.\n\nSprint 2 is over."]
    # Registered under the oldest, and what was said with it is settled with it.
    assert recorder.events == [first]
    assert await remaining(sessions) == []
    assert recorder.releases == 1


async def test_ag_cue_029_an_empty_queue_takes_no_lease(sessions):
    """AG-CUE-029 — tests/brd/tg_agent_shell/agents.feature"""
    recorder = Recorder()

    assert await tick(sessions, **_hooks(recorder)) is False

    assert not recorder.said and recorder.releases == 0
    async with sessions() as session:
        assert await waiting_cues(session) == []


def _shown_review(reviews: ProposalStore, *, minutes_ago: int):
    proposal = reviews.open_proposal(
        message="Готово?",
        workspace_revision=1,
        changes=[
            ProposalChange(entity="card", action=ChangeAction.CREATE, values={"title": "Рынок"})
        ],
    )
    proposal.shown_at = utcnow() - timedelta(minutes=minutes_ago)
    return proposal


async def test_pr_expire_029_the_review_out_of_time_is_closed_before_the_cue_is_said(sessions):
    """PR-EXPIRE-029 — tests/brd/tg_agent_shell/proposals.feature"""
    turn, reviews = TurnManager(), ProposalStore()
    proposal = _shown_review(reviews, minutes_ago=PROPOSAL_REVIEW_MINUTES)
    await write(sessions, "Sprint 1 is over.")
    runtime = _runtime(sessions, turn, reviews)
    recorder = Recorder()
    closed: list[int] = []

    async def expire() -> None:
        review = reviews.expired(utcnow())
        if review is not None:
            closed.append(review.id)
            reviews.end_proposal(review.id)

    delivered = await tick(
        sessions,
        gate=runtime.can_speak,
        speak=recorder.speak,
        release=runtime.release,
        expire=expire,
    )

    # The screen is closed first, so the gate is open by the time this tick reads it.
    assert delivered is True
    assert closed == [proposal.id]
    assert recorder.said == ["Sprint 1 is over."]
    assert await remaining(sessions) == []


async def test_pr_expire_029_a_review_still_within_its_time_keeps_the_gate_shut(sessions):
    """PR-EXPIRE-029 — tests/brd/tg_agent_shell/proposals.feature"""
    turn, reviews = TurnManager(), ProposalStore()
    proposal = _shown_review(reviews, minutes_ago=PROPOSAL_REVIEW_MINUTES - 1)
    await write(sessions, "Sprint 1 is over.")
    runtime = _runtime(sessions, turn, reviews)
    recorder = Recorder()

    assert reviews.expired(utcnow()) is None
    assert (
        await tick(
            sessions,
            gate=runtime.can_speak,
            speak=recorder.speak,
            release=runtime.release,
            expire=runtime.expire_review,
        )
        is False
    )

    assert reviews.proposal(proposal.id) is proposal
    assert not recorder.said
    assert await remaining(sessions) == ["Sprint 1 is over."]


async def test_pl_end_015_the_sprints_own_words_are_what_reaches_the_owner(sessions):
    """PL-END-015 — tests/brd/planning.feature"""
    async with sessions() as session:
        await create_card(
            session, title="Shipped", kind="action", stage="sprint", effort_points=5
        )
        sprint = await start_sprint(session, success_criteria="Ship v2")
        await finish_sprint(session)
        await session.commit()
        number, sprint_id = sprint.number, sprint.id
    # The ending, handed on, is the Sprint summary hook's one pending request.
    await queue_advice(REGISTRY.hooks, sessions, Committed(SPRINT_ENDED, sprint_id))
    recorder = Recorder()

    async def prepare(hook: str, payload: list) -> str | None:
        return await REGISTRY.hooks.prepare(sessions, hook, payload)

    assert await tick(sessions, **_hooks(recorder), prepare=prepare) is True

    said = recorder.said[0]
    assert said.startswith(f"Sprint {number} is over")
    assert f"[Sprint retro](retro:{sprint_id})" in said
    # Nothing dresses it as a Reminder that went off: the words are the Sprint's own.
    assert "Reminder" not in said
    assert await remaining(sessions) == []
