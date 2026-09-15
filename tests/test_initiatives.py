"""A hook's pending request: from a committed change to the words said, independent of Safwa."""

from __future__ import annotations

from sqlalchemy import select, text

from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed, queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_cue, drop_hook_cue, merge_hook_cue
from tg_agent_shell.foundation.changes import Committed, record_change
from tg_agent_shell.hooks.contracts import Advise, HookSpec, HookSwitch, OnCommitted
from tg_agent_shell.hooks.registry import HookRegistry

KIND = "thing.changed"


async def subject(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def words(session, items) -> str | None:
    # What is "still there" is the test's to say: even ids are gone.
    left = [item for item in items if item % 2]
    return f"About {', '.join(str(item) for item in left)}." if left else None


HOOK = HookSpec(
    name="test.advice", owner="test", on=(OnCommitted(kind=KIND),),
    evaluate=subject, effect=Advise(words),
    switch=HookSwitch(title="Advice", description="Asks about a changed thing."),
)


def catalogue(*specs, policy=None) -> HookRegistry:
    extra = {"policy": policy} if policy is not None else {}
    return HookRegistry.of(specs, owners=frozenset({"test"}), **extra)


class Recorder:
    def __init__(self, *, open_gate=True):
        self.open_gate = open_gate
        self.said: list[str] = []

    async def gate(self) -> bool:
        return self.open_gate

    async def speak(self, event_id: str, text: str) -> bool:
        self.said.append(text)
        return True


async def pending(sessions) -> list[tuple[str | None, str | None, list | None]]:
    async with sessions() as session:
        return [
            (cue.hook, cue.text, cue.payload)
            for cue in await session.scalars(select(Cue).order_by(Cue.id))
        ]


async def test_ag_hook_037_a_commit_hands_the_change_on_and_a_rollback_does_not(sessions):
    """AG-HOOK-037 — tests/brd/tg_agent_shell/agents.feature"""
    sink = bind_committed(sessions, catalogue(HOOK))
    async with sessions() as session:
        # An operation has read before it records, so the transaction is real.
        await session.execute(text("SELECT 1"))
        record_change(session, KIND, 1)
        await session.rollback()
        await session.execute(text("SELECT 1"))
        record_change(session, KIND, 3)
        await session.commit()
    await sink.drain()
    assert await pending(sessions) == [("test.advice", None, [3])]

    async with sessions() as session:
        await session.execute(text("SELECT 1"))
        record_change(session, "other.kind", 5)
        await session.commit()
    await sink.drain()
    assert await pending(sessions) == [("test.advice", None, [3])]


async def test_ag_hook_038_a_second_change_adds_to_the_one_pending_request(sessions):
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    await queue_advice(hooks, sessions, Committed(KIND, 5))
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    assert await pending(sessions) == [("test.advice", None, [3, 5])]

    async with sessions() as session:
        await merge_hook_cue(session, hook="test.other", items=[1])
        await session.commit()
    assert [row[0] for row in await pending(sessions)] == ["test.advice", "test.other"]


async def test_ag_hook_038_the_words_are_made_when_the_request_is_next_in_line(sessions):
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    await queue_advice(hooks, sessions, Committed(KIND, 4))
    recorder = Recorder()

    async def prepare(hook, payload):
        return await hooks.prepare(sessions, hook, payload)

    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, prepare=prepare) is True
    assert recorder.said == ["About 3."]
    assert await pending(sessions) == []

    # Nothing left to ask about: the request is dropped without a turn, and what follows
    # is said on the next tick.
    await queue_advice(hooks, sessions, Committed(KIND, 4))
    async with sessions() as session:
        await add_cue(session, text="Sprint 1 is over.")
        await session.commit()
    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, prepare=prepare) is False
    assert await pending(sessions) == [(None, "Sprint 1 is over.", None)]
    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, prepare=prepare) is True
    assert recorder.said == ["About 3.", "Sprint 1 is over."]


async def test_ag_hook_038_a_switched_off_hook_says_nothing_and_a_dropped_request_stays_gone(sessions):
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    off_names: set[str] = set()

    async def policy(session, name):
        return name not in off_names

    hooks = catalogue(HOOK, policy=policy)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    off_names.add(HOOK.name)
    recorder = Recorder()

    async def prepare(hook, payload):
        return await hooks.prepare(sessions, hook, payload)

    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, prepare=prepare) is False
    assert recorder.said == []
    assert await pending(sessions) == []

    # Dropping is what the application does when the switch is turned; nothing revives it.
    off_names.clear()
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    async with sessions() as session:
        await drop_hook_cue(session, HOOK.name)
        await session.commit()
    assert await pending(sessions) == []
