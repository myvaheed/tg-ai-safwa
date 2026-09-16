"""A hook's pending request: from a committed change to the words said, independent of Safwa."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed, queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_cue, drop_hook_cue, merge_hook_cue
from tg_agent_shell.foundation.changes import Committed, record_change
from tg_agent_shell.hooks.contracts import (
    Advise,
    HookSpec,
    HookSwitch,
    OnCommitted,
    OnTick,
    Tick,
)
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.hooks.ticks import TickSchedule

KIND = "thing.changed"
TZ = ZoneInfo("Europe/Istanbul")


def local(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in September 2026 by the workspace's clock, as the process sees it."""
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ).astimezone(UTC)


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


async def marker(event: Tick) -> tuple[str, ...]:
    return (event.at,)


async def morning_words(session, items) -> str | None:
    return "Morning."


DAILY = HookSpec(
    name="test.daily", owner="test", on=(OnTick(at="09:00"),),
    evaluate=marker, effect=Advise(morning_words),
    switch=HookSwitch(title="Daily", description="Asks every morning."),
)


def catalogue(*specs, policy=None) -> HookRegistry:
    extra = {"policy": policy} if policy is not None else {}
    return HookRegistry.of(specs, owners=frozenset({"test"}), **extra)


class Recorder:
    def __init__(self, *, open_gate=True):
        self.open_gate = open_gate
        self.said: list[str] = []
        self.events: list[str] = []
        self.releases = 0

    async def gate(self) -> bool:
        return self.open_gate

    async def speak(self, event_id: str, text: str) -> bool:
        self.events.append(event_id)
        self.said.append(text)
        return True

    def release(self) -> None:
        self.releases += 1


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


async def test_ag_hook_038_what_the_hook_adds_while_the_request_is_said_is_owed_still(sessions):
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    recorder = Recorder()

    async def prepare(hook, payload):
        return await hooks.prepare(sessions, hook, payload)

    async def speak_while_another_arrives(event_id, text):
        await queue_advice(hooks, sessions, Committed(KIND, 5))
        return await recorder.speak(event_id, text)

    assert await tick(sessions, gate=recorder.gate, speak=speak_while_another_arrives, prepare=prepare) is True
    assert recorder.said == ["About 3."]
    assert await pending(sessions) == [("test.advice", None, [5])]

    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, prepare=prepare) is True
    assert recorder.said == ["About 3.", "About 5."]
    # The second request is its own: the message registered under the first is not it.
    assert recorder.events[0] != recorder.events[1]
    assert await pending(sessions) == []


async def test_ag_hook_038_a_request_whose_words_cannot_be_made_stays_owed(sessions):
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    recorder = Recorder()
    broken = True

    async def prepare(hook, payload):
        if broken:
            raise RuntimeError("cannot read")
        return await hooks.prepare(sessions, hook, payload)

    with pytest.raises(RuntimeError, match="cannot read"):
        await tick(sessions, gate=recorder.gate, speak=recorder.speak, release=recorder.release, prepare=prepare)
    assert recorder.said == []
    assert recorder.releases == 1
    assert await pending(sessions) == [("test.advice", None, [3])]

    broken = False
    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, release=recorder.release, prepare=prepare) is True
    assert recorder.said == ["About 3."]
    assert recorder.releases == 2


async def test_ag_hook_038_nothing_is_read_while_the_chat_is_busy(sessions):
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    recorder = Recorder(open_gate=False)
    reads = 0

    async def prepare(hook, payload):
        nonlocal reads
        reads += 1
        return await hooks.prepare(sessions, hook, payload)

    for _ in range(3):
        assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, release=recorder.release, prepare=prepare) is False
    assert reads == 0 and recorder.releases == 0
    assert await pending(sessions) == [("test.advice", None, [3])]

    recorder.open_gate = True
    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, release=recorder.release, prepare=prepare) is True
    assert reads == 1 and recorder.releases == 1


async def test_ag_hook_039_a_daily_check_comes_due_once_a_day_and_not_for_the_day_it_missed(sessions):
    """AG-HOOK-039 — tests/brd/tg_agent_shell/agents.feature"""
    schedule = TickSchedule(catalogue(DAILY).tick_times, now=local(16, 8, 0), tz=TZ)
    assert schedule.due(local(16, 8, 30)) == []
    assert schedule.due(local(16, 9, 0)) == [Tick("09:00")]
    assert [schedule.due(local(16, 9, minute)) for minute in (1, 30, 59)] == [[], [], []]
    assert schedule.due(local(16, 23, 59)) == []
    assert schedule.due(local(17, 9, 1)) == [Tick("09:00")]

    # Started after the time: the day it missed is not run, the next day's is.
    restarted = TickSchedule({"09:00"}, now=local(16, 15, 0), tz=TZ)
    assert restarted.due(local(16, 15, 1)) == []
    assert restarted.due(local(17, 9, 0)) == [Tick("09:00")]

    # Two checks at different times come due each at its own, and once.
    two = TickSchedule({"21:30", "09:00"}, now=local(16, 8, 0), tz=TZ)
    assert two.due(local(16, 9, 5)) == [Tick("09:00")]
    assert two.due(local(16, 21, 30)) == [Tick("21:30")]
    assert two.due(local(17, 8, 0)) == []


async def test_ag_hook_039_a_switched_off_check_does_not_run_and_is_not_run_late_once_on(sessions):
    """AG-HOOK-039 — tests/brd/tg_agent_shell/agents.feature"""
    off_names = {DAILY.name}

    async def policy(session, name):
        return name not in off_names

    hooks = catalogue(DAILY, policy=policy)
    schedule = TickSchedule(hooks.tick_times, now=local(16, 8, 0), tz=TZ)
    for due in schedule.due(local(16, 9, 0)):
        await queue_advice(hooks, sessions, due)
    assert await pending(sessions) == []

    off_names.clear()
    for due in schedule.due(local(16, 9, 5)):
        await queue_advice(hooks, sessions, due)
    assert await pending(sessions) == []

    for due in schedule.due(local(17, 9, 0)):
        await queue_advice(hooks, sessions, due)
    assert await pending(sessions) == [("test.daily", None, ["09:00"])]
    # Fired again before it is said, it is the one request still.
    await queue_advice(hooks, sessions, Tick("09:00"))
    assert await pending(sessions) == [("test.daily", None, ["09:00"])]
