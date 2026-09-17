"""A hook's pending request: from a committed change to the words said, independent of Safwa."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed, queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_cue, drop_hook_cue, merge_hook_cue
from tg_agent_shell.foundation.changes import Committed, record_change
from tg_agent_shell.hooks.contracts import (
    Advise,
    HookSpec,
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
    title="Advice", description="Asks about a changed thing.",
)


async def marker(event: Tick) -> tuple[str, ...]:
    return (event.at,)


async def morning_words(session, items) -> str | None:
    return "Morning."


NINE = time(9, 0)


async def at_nine(session) -> time:
    return NINE


DAILY = HookSpec(
    name="test.daily", owner="test", on=(OnTick(at=at_nine),),
    evaluate=marker, effect=Advise(morning_words),
    title="Daily", description="Asks every morning.",
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

    # Nothing left to ask about: the request is dropped without words, and what waits
    # beside it is said on the same tick, without it.
    await queue_advice(hooks, sessions, Committed(KIND, 4))
    async with sessions() as session:
        await add_cue(session, text="Sprint 1 is over.")
        await session.commit()
    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, prepare=prepare) is True
    assert recorder.said == ["About 3.", "Sprint 1 is over."]
    assert await pending(sessions) == []
    # Nothing left at all: no turn is taken.
    await queue_advice(hooks, sessions, Committed(KIND, 6))
    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, prepare=prepare) is False
    assert await pending(sessions) == []


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

    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, release=recorder.release, prepare=prepare) is False
    assert recorder.said == []
    assert recorder.releases == 1
    assert await pending(sessions) == [("test.advice", None, [3])]

    broken = False
    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, release=recorder.release, prepare=prepare) is True
    assert recorder.said == ["About 3."]
    assert recorder.releases == 2


async def test_ag_hook_038_several_requests_owed_at_once_are_each_worded_and_said_in_one_turn(sessions):
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK, DAILY)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    await queue_advice(hooks, sessions, Tick("09:00", at_nine))
    async with sessions() as session:
        await add_cue(session, text="1 Reminder triggered.")
        await session.commit()
    recorder = Recorder()
    broken = {"test.daily"}

    async def prepare(hook, payload):
        if hook in broken:
            raise RuntimeError("cannot read")
        return await hooks.prepare(sessions, hook, payload)

    # The one whose words cannot be made stays owed alone; the others are said together.
    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, release=recorder.release, prepare=prepare) is True
    assert recorder.said == ["About 3.\n\n1 Reminder triggered."]
    assert await pending(sessions) == [("test.daily", None, ["09:00"])]

    broken.clear()
    assert await tick(sessions, gate=recorder.gate, speak=recorder.speak, release=recorder.release, prepare=prepare) is True
    assert recorder.said[-1] == "Morning."
    assert await pending(sessions) == []


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
    async def at_ten(session) -> time:
        return time(10, 0)

    nine = Tick("09:00", at_nine)
    schedule = TickSchedule(now=local(16, 8, 0), tz=TZ)
    assert schedule.due(local(16, 8, 30), {at_nine: NINE}) == []
    assert schedule.due(local(16, 9, 0), {at_nine: NINE}) == [nine]
    assert [schedule.due(local(16, 9, minute), {at_nine: NINE}) for minute in (1, 30, 59)] == [[]] * 3
    assert schedule.due(local(16, 23, 59), {at_nine: NINE}) == []
    assert schedule.due(local(17, 9, 1), {at_nine: NINE}) == [nine]

    # Started after the time: the day it missed is not run, the next day's is.
    restarted = TickSchedule(now=local(16, 15, 0), tz=TZ)
    assert restarted.due(local(16, 15, 1), {at_nine: NINE}) == []
    assert restarted.due(local(17, 9, 0), {at_nine: NINE}) == [nine]

    # The times are handed to every look: moved later, one comes due again that day at the
    # new time; moved to one already passed, it waits for tomorrow's.
    moved = TickSchedule(now=local(16, 8, 0), tz=TZ)
    assert moved.due(local(16, 9, 5), {at_nine: NINE}) == [nine]
    assert moved.due(local(16, 21, 30), {at_nine: time(21, 30)}) == [Tick("21:30", at_nine)]
    assert moved.due(local(16, 22, 0), {at_nine: time(21, 0)}) == []
    assert moved.due(local(17, 21, 0), {at_nine: time(21, 0)}) == [Tick("21:00", at_nine)]

    # Two readers in one look: each passing is its own Tick, in the order given, and one
    # look that sees both passed hands both on.
    two = TickSchedule(now=local(16, 8, 0), tz=TZ)
    assert two.due(local(16, 9, 30), {at_nine: NINE, at_ten: time(10, 0)}) == [nine]
    assert two.due(local(16, 10, 0), {at_nine: NINE, at_ten: time(10, 0)}) == [Tick("10:00", at_ten)]
    assert two.due(local(17, 10, 30), {at_nine: NINE, at_ten: time(10, 0)}) == [nine, Tick("10:00", at_ten)]


async def test_ag_hook_039_a_switched_off_check_does_not_run_and_is_not_run_late_once_on(sessions):
    """AG-HOOK-039 — tests/brd/tg_agent_shell/agents.feature"""
    off_names = {DAILY.name}

    async def policy(session, name):
        return name not in off_names

    hooks = catalogue(DAILY, policy=policy)
    schedule = TickSchedule(now=local(16, 8, 0), tz=TZ)

    async def look(now: datetime) -> None:
        async with sessions() as session:
            clocks = {clock: await clock(session) for clock in hooks.daily_clocks}
        for due in schedule.due(now, clocks):
            await queue_advice(hooks, sessions, due)

    await look(local(16, 9, 0))
    assert await pending(sessions) == []

    off_names.clear()
    await look(local(16, 9, 5))
    assert await pending(sessions) == []

    await look(local(17, 9, 0))
    assert await pending(sessions) == [("test.daily", None, ["09:00"])]
    # Fired again before it is said, it is the one request still.
    await queue_advice(hooks, sessions, Tick("09:00", at_nine))
    assert await pending(sessions) == [("test.daily", None, ["09:00"])]
