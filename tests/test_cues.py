"""The Cue queue: the row outlives the turn until the owner has the words."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from safwa.cues.background import tick
from safwa.cues.queue import cue_advisor, next_cue
from safwa.cues.runtime import CueRuntime
from safwa.domain import create_card, finish_sprint, start_sprint
from safwa.features.proposals.model import ChangeAction, ProposalChange
from safwa.features.proposals.store import ProposalStore
from safwa.foundation.clock import utcnow
from safwa.models import AgentRun, Cue
from safwa.turn import TurnManager


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
        sessions=sessions, turn=turn, advisor=SimpleNamespace(reviews=reviews)
    )
    return CueRuntime(services, bot=None, owner_id=1)  # type: ignore[arg-type]


async def test_ag_turn_015_the_gate_is_shut_while_anything_of_the_owners_is_open(sessions):
    """AG-TURN-015 — tests/brd/agents.feature"""
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
    """AG-TURN-015 — tests/brd/agents.feature"""
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
        cue = await cue_advisor(session, text=text)
        await session.commit()
        return cue.id


async def remaining(sessions) -> list[str]:
    async with sessions() as session:
        return [cue.text for cue in await session.scalars(select(Cue).order_by(Cue.id))]


async def test_pl_end_015_a_waiting_cue_is_said_once_and_then_gone(sessions):
    """PL-END-015 — tests/brd/planning.feature"""
    await write(sessions, "Sprint 1 is over.")
    recorder = Recorder()

    assert await tick(sessions, **_hooks(recorder)) is True

    assert recorder.said == ["Sprint 1 is over."]
    assert len(recorder.events[0]) == 32
    assert await remaining(sessions) == []
    assert await tick(sessions, **_hooks(recorder)) is False


async def test_pl_end_015_a_closed_gate_leaves_the_cue_waiting(sessions):
    """PL-END-015 — tests/brd/planning.feature"""
    await write(sessions, "Sprint 1 is over.")
    recorder = Recorder(open_gate=False)

    assert await tick(sessions, **_hooks(recorder)) is False

    assert not recorder.said
    assert await remaining(sessions) == ["Sprint 1 is over."]
    # No lease was taken, so none is given back.
    assert recorder.releases == 0


async def test_pl_end_015_a_turn_that_did_not_land_leaves_the_cue_waiting(sessions):
    """PL-END-015 — tests/brd/planning.feature"""
    await write(sessions, "Sprint 1 is over.")
    failing = Recorder(delivered=False)

    assert await tick(sessions, **_hooks(failing)) is False

    assert failing.said  # it was attempted
    assert await remaining(sessions) == ["Sprint 1 is over."]
    assert failing.releases == 1

    recorder = Recorder()
    assert await tick(sessions, **_hooks(recorder)) is True
    assert recorder.said == ["Sprint 1 is over."]


async def test_pl_end_015_one_tick_says_the_oldest_cue_and_leaves_the_rest(sessions):
    """PL-END-015 — tests/brd/planning.feature"""
    await write(sessions, "Sprint 1 is over.")
    await write(sessions, "Sprint 2 is over.")
    recorder = Recorder()

    assert await tick(sessions, **_hooks(recorder)) is True

    assert recorder.said == ["Sprint 1 is over."]
    assert await remaining(sessions) == ["Sprint 2 is over."]
    assert recorder.releases == 1


async def test_pl_end_015_an_empty_queue_takes_no_lease(sessions):
    """PL-END-015 — tests/brd/planning.feature"""
    recorder = Recorder()

    assert await tick(sessions, **_hooks(recorder)) is False

    assert not recorder.said and recorder.releases == 0
    async with sessions() as session:
        assert await next_cue(session) is None


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
    recorder = Recorder()

    assert await tick(sessions, **_hooks(recorder)) is True

    said = recorder.said[0]
    assert said.startswith(f"Sprint {number} is over")
    assert f"[Sprint retro](retro:{sprint_id})" in said
    # Nothing dresses it as a Reminder that went off: the words are the Sprint's own.
    assert "Reminder" not in said
    assert await remaining(sessions) == []
