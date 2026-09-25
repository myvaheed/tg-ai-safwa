"""A hook's pending request: from a committed change to the words said, independent of Safwa."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import TickPoll, bind_committed, queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_cue, add_hook_cue, drop_hook_cue
from tg_agent_shell.foundation.changes import Committed, record_change
from tg_agent_shell.hooks.contracts import (
    Advise,
    HookSpec,
    OnCommitted,
    OnTick,
    Run,
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

    async def speak(self, event_id: str, text: str, shown: tuple[str, ...] = ()) -> bool:
        self.events.append(event_id)
        self.said.append(text)
        return True

    async def delivered(self, event_id: str) -> bool:
        return event_id in self.events

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


async def test_ag_hook_037_a_commits_work_is_done_after_its_facts_are_handed_on(sessions):
    """AG-HOOK-037 — tests/brd/tg_agent_shell/agents.feature"""
    order: list[str] = []
    started: dict[int, asyncio.Event] = {1: asyncio.Event(), 3: asyncio.Event()}
    release = asyncio.Event()

    async def slow_work(payload, context):
        assert context.resources == "the features"
        order.append(f"work {payload} started")
        started[payload].set()
        await release.wait()
        order.append(f"work {payload} done")

    async def broken_work(payload, context):
        raise RuntimeError("no")

    work = HookSpec(
        name="test.work", owner="test", on=(OnCommitted(kind=KIND),),
        evaluate=subject, effect=Run(slow_work),
        title="Work", description="Does something slow with a changed thing.",
    )
    broken = HookSpec(
        name="test.broken", owner="test", on=(OnCommitted(kind=KIND),),
        evaluate=subject, effect=Run(broken_work),
        title="Broken", description="Fails.",
    )
    # The broken one first: its failure stops nothing behind it.
    sink = bind_committed(sessions, catalogue(HOOK, broken, work), resources="the features")
    async with sessions() as session:
        await session.execute(text("SELECT 1"))
        record_change(session, KIND, 1)
        await session.commit()
    await started[1].wait()
    # The request of the first commit is written while its work still runs, and the next
    # commit is handed on — its request written, its work begun — without waiting for it.
    assert await pending(sessions) == [("test.advice", None, [1])]
    async with sessions() as session:
        await session.execute(text("SELECT 1"))
        record_change(session, KIND, 3)
        await session.commit()
    await started[3].wait()
    assert await pending(sessions) == [("test.advice", None, [1]), ("test.advice", None, [3])]
    assert "work 1 done" not in order
    release.set()
    await sink.drain()
    assert sorted(order) == ["work 1 done", "work 1 started", "work 3 done", "work 3 started"]


async def test_ag_hook_038_a_second_change_is_worded_with_the_first_as_one_request(sessions):
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    await queue_advice(hooks, sessions, Committed(KIND, 5))
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    # Each firing is written down as it is: a row, never an edit of an earlier one.
    assert await pending(sessions) == [
        ("test.advice", None, [3]), ("test.advice", None, [5]), ("test.advice", None, [3]),
    ]
    async with sessions() as session:
        await add_hook_cue(session, hook="test.other", items=[1])
        await session.commit()
    recorder = Recorder()
    asked: list[tuple[str, list]] = []

    async def prepare(hook, payload):
        asked.append((hook, payload))
        return await hooks.prepare(sessions, hook, payload) if hook == HOOK.name else None

    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, prepare=prepare) is True
    # The hook is asked for words once, about everything it wrote down, each thing once.
    assert asked == [("test.advice", [3, 5]), ("test.other", [1])]
    assert recorder.said == ["About 3, 5."]
    assert await pending(sessions) == []


async def test_ag_hook_038_the_words_are_made_when_the_request_is_next_in_line(sessions):
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    await queue_advice(hooks, sessions, Committed(KIND, 4))
    recorder = Recorder()

    async def prepare(hook, payload):
        return await hooks.prepare(sessions, hook, payload)

    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, prepare=prepare) is True
    assert recorder.said == ["About 3."]
    assert await pending(sessions) == []

    # Nothing left to ask about: the request is dropped without words, and what waits
    # beside it is said on the same tick, without it.
    await queue_advice(hooks, sessions, Committed(KIND, 4))
    async with sessions() as session:
        await add_cue(session, text="Sprint 1 is over.")
        await session.commit()
    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, prepare=prepare) is True
    assert recorder.said == ["About 3.", "Sprint 1 is over."]
    assert await pending(sessions) == []
    # Nothing left at all: no turn is taken.
    await queue_advice(hooks, sessions, Committed(KIND, 6))
    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, prepare=prepare) is False
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

    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, prepare=prepare) is False
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

    async def speak_while_another_arrives(event_id, text, shown):
        await queue_advice(hooks, sessions, Committed(KIND, 5))
        return await recorder.speak(event_id, text)

    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=speak_while_another_arrives, prepare=prepare) is True
    assert recorder.said == ["About 3."]
    assert await pending(sessions) == [("test.advice", None, [5])]

    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, prepare=prepare) is True
    assert recorder.said == ["About 3.", "About 5."]
    # The second request is its own: the message registered under the first is not it.
    assert recorder.events[0] != recorder.events[1]
    assert await pending(sessions) == []


async def test_ag_cue_029_a_hooks_rows_a_turn_said_are_settled_by_its_message_alone(sessions):
    """AG-CUE-029 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK)
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    async with sessions() as session:
        await add_cue(session, text="1 Reminder triggered.")
        await session.commit()
    recorder = Recorder()
    worded: list[list] = []

    async def prepare(hook, payload):
        worded.append(payload)
        return await hooks.prepare(sessions, hook, payload)

    async def register_then_stop(event_id, text, shown):
        await recorder.speak(event_id, text)
        raise RuntimeError("the process stopped between the message and the settling")

    with pytest.raises(RuntimeError):
        await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=register_then_stop, prepare=prepare)
    assert recorder.said == ["About 3.\n\n1 Reminder triggered."]
    # Fired again since: a row of its own, owed still.
    await queue_advice(hooks, sessions, Committed(KIND, 5))

    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, prepare=prepare) is True

    # Nothing said with the registered message is worded or said again — not even asked
    # about — and the row written since is the next request, under its own id.
    assert worded == [[3], [5]]
    assert recorder.said[-1] == "About 5."
    assert len(set(recorder.events)) == 2
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

    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, release=recorder.release, prepare=prepare) is False
    assert recorder.said == []
    assert recorder.releases == 1
    assert await pending(sessions) == [("test.advice", None, [3])]

    broken = False
    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, release=recorder.release, prepare=prepare) is True
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
    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, release=recorder.release, prepare=prepare) is True
    assert recorder.said == ["About 3.\n\n1 Reminder triggered."]
    assert await pending(sessions) == [("test.daily", None, ["09:00"])]

    broken.clear()
    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, release=recorder.release, prepare=prepare) is True
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
        assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, release=recorder.release, prepare=prepare) is False
    assert reads == 0 and recorder.releases == 0
    assert await pending(sessions) == [("test.advice", None, [3])]

    recorder.open_gate = True
    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, release=recorder.release, prepare=prepare) is True
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
    poll = TickPoll(hooks, sessions, resources=None, timezone="Europe/Istanbul", now=local(16, 8, 0))

    await poll.look(local(16, 9, 0))
    assert await pending(sessions) == []

    off_names.clear()
    await poll.look(local(16, 9, 5))
    assert await pending(sessions) == []

    await poll.look(local(17, 9, 0))
    assert await pending(sessions) == [("test.daily", None, ["09:00"])]
    # Fired again before it is said, it is the one request still.
    await queue_advice(hooks, sessions, Tick("09:00", at_nine))
    assert await pending(sessions) == [("test.daily", None, ["09:00"])] * 2
    recorder = Recorder()
    asked: list[tuple[str, list]] = []

    async def prepare(hook, payload):
        asked.append((hook, payload))
        return await hooks.prepare(sessions, hook, payload)

    assert await tick(sessions, gate=recorder.gate, delivered=recorder.delivered, speak=recorder.speak, prepare=prepare) is True
    assert asked == [("test.daily", ["09:00"])] and recorder.said == ["Morning."]
    assert await pending(sessions) == []


async def test_ag_hook_039_a_daily_request_not_said_by_midnight_is_dropped(sessions):
    """AG-HOOK-039 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(HOOK, DAILY)
    poll = TickPoll(hooks, sessions, resources=None, timezone="Europe/Istanbul", now=local(16, 8, 0))
    await poll.look(local(16, 9, 0))
    await queue_advice(hooks, sessions, Committed(KIND, 3))
    assert await pending(sessions) == [("test.daily", None, ["09:00"]), ("test.advice", None, [3])]

    # Still that day: owed. The day over: the morning's request is gone, the other stands.
    await poll.look(local(16, 23, 59))
    assert await pending(sessions) == [("test.daily", None, ["09:00"]), ("test.advice", None, [3])]
    async with sessions() as session:
        for cue in await session.scalars(select(Cue)):
            cue.created_at = local(16, 9, 0)
        await session.commit()
    await poll.look(local(17, 0, 1))
    assert await pending(sessions) == [("test.advice", None, [3])]
    # One a turn is saying is that turn's to settle, not the midnight's to drop.
    await poll.look(local(17, 9, 0))
    async with sessions() as session:
        for cue in await session.scalars(select(Cue).where(Cue.hook == DAILY.name)):
            cue.created_at, cue.event_id = local(17, 9, 0), "a" * 32
        await session.commit()
    await poll.look(local(18, 0, 1))
    assert [row[0] for row in await pending(sessions)] == ["test.advice", "test.daily"]


async def test_ag_hook_039_work_of_a_daily_check_that_failed_is_tried_again_until_done(sessions):
    """AG-HOOK-039 — tests/brd/tg_agent_shell/agents.feature"""
    attempts: list[str] = []
    failing = True

    async def flaky(marker, context):
        attempts.append(marker)
        if failing:
            raise RuntimeError("the database was busy")

    work = HookSpec(
        name="test.work", owner="test", on=(OnTick(at=at_nine),),
        evaluate=marker, effect=Run(flaky),
        title="Work", description="Does something at nine.",
    )
    hooks = catalogue(work, DAILY)
    poll = TickPoll(hooks, sessions, resources=None, timezone="Europe/Istanbul", now=local(16, 8, 0))

    await poll.look(local(16, 9, 0))
    assert attempts == ["09:00"]
    assert await pending(sessions) == [("test.daily", None, ["09:00"])]
    # Every later look tries the work again; the request beside it is not written twice.
    await poll.look(local(16, 9, 1))
    assert attempts == ["09:00"] * 2
    assert await pending(sessions) == [("test.daily", None, ["09:00"])]
    failing = False
    await poll.look(local(16, 9, 2))
    await poll.look(local(16, 9, 3))
    assert attempts == ["09:00"] * 3
