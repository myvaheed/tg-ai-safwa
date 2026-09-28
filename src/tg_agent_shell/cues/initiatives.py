"""From a committed change, a time of day or the start, to a hook's pending request or its
work.

An operation records what it changed beside its transaction (`foundation/changes.py`).
After the commit, the one listener below hands those facts to the hooks that subscribe to
that kind of change, and whatever an Advise check returns is written down as one `Cue`
row of that hook. A rollback leaves nothing to hand on. The one tick poll hands a `Tick` to
the daily hooks when the time of day their reader names passes, and to the hooks that run
every so long when their interval runs out, by the same path. A Run hook does its work there
and then: on a tick inside the poll, with the owner's chat to publish to while it is free; on
a commit once that commit's facts are handed on, outside the order they are kept in, with no
chat; at the start before the first message is taken, with no chat either.

The facts are handed on outside the transaction that made them: a process that dies in
between loses one request, never the change itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from ..foundation.changes import CHANGES, Committed, take_changes
from ..foundation.clock import utcnow
from ..foundation.poll import run_poll
from ..hooks.contracts import Advise, ChatState, OnTick, Run, RunContext, Started, Tick
from ..hooks.registry import HookEvent, HookRegistry
from ..hooks.ticks import TickSchedule
from .queue import add_hook_cue, forget_before

logger = logging.getLogger(__name__)

_SINK = "committed_sink"

# One Run's work, ready to be awaited where the caller can let it take its time; it says
# whether it was done, and has logged its failure if not.
Work = Callable[[], Awaitable[bool]]


async def hand_on(
    hooks: HookRegistry,
    sessions: async_sessionmaker[AsyncSession],
    event: HookEvent,
    *,
    work: RunContext | None = None,
) -> list[Work]:
    """Check one event against the hooks: keep each Advise result as a pending request now,
    and hand back each Run's work — with `work`, which the adapter brings wherever the
    registry admits a Run — for the caller to do when it is free to."""
    jobs: list[Work] = []
    async for checked in hooks.evaluate(event, sessions):
        if checked.error is not None:
            logger.error("Hook %s failed: %s", checked.spec.name, checked.error)
            continue
        if not checked.payloads:
            continue
        match checked.spec.effect:
            case Advise():
                async with sessions() as session:
                    await add_hook_cue(session, hook=checked.spec.name, items=checked.payloads)
                    await session.commit()
            case Run(run=run):
                assert work is not None  # the registry admits a Run only where the adapter brings its context
                for payload in checked.payloads:
                    jobs.append(partial(_guarded, run, payload, work, checked.spec.name))
    return jobs


async def _guarded(run: Any, payload: Any, work: RunContext, name: str) -> bool:
    try:
        await run(payload, work)
    except Exception:
        logger.exception("The work of the hook %s failed", name)
        return False
    return True


async def queue_advice(
    hooks: HookRegistry,
    sessions: async_sessionmaker[AsyncSession],
    event: HookEvent,
    *,
    work: RunContext | None = None,
) -> list[Work]:
    """`hand_on`, and do each Run's work here and now; the work that failed is handed back."""
    return [job for job in await hand_on(hooks, sessions, event, work=work) if not await job()]


async def _no_chat(text: str, kind: str) -> None:
    raise RuntimeError("A hook off the dialogue has no chat to publish to: record a change, and let an Advise hook say it")


async def hand_on_start(
    hooks: HookRegistry, sessions: async_sessionmaker[AsyncSession], *, resources: Any
) -> None:
    """Hand `Started` to the hooks once, before the first message is taken, and do their work
    there and then. Work that fails stops the start: what a restart owes is not optional."""
    work = RunContext(
        resources=resources, still_current=lambda: True, publish=_no_chat, sessions=sessions
    )
    if await queue_advice(hooks, sessions, Started(), work=work):
        raise RuntimeError("The work of a hook on start failed; the log above says which")


@dataclass(frozen=True, slots=True)
class TickChat:
    """The owner's chat, as the checks on a schedule see it and speak in it."""

    # When the owner last acted.
    owner_acted_at: Callable[[], datetime]
    # Whether a message could go now.
    free: Callable[[], Awaitable[bool]]
    # The kind and time of the newest message kept in the chat, or None while it keeps none.
    newest: Callable[[], Awaitable[tuple[str, datetime | None] | None]]
    # Puts a message in the chat while it is free, and not once `still_current` turns.
    speak: Callable[[str, str, Callable[[], bool]], Awaitable[None]]


class TickPoll:
    """Hands the checks on a schedule their Tick when their time comes: once, however many
    looks.

    A look also drops a daily hook's request the local midnight has passed since — the day
    it was about is over — and tries again the work an earlier look could not finish, until
    it is done: a Sprint's midnight end is not optional the way a request is. The work of a
    check that runs every so long is not tried again: its next interval is its next try.
    """

    def __init__(
        self,
        hooks: HookRegistry,
        sessions: async_sessionmaker[AsyncSession],
        *,
        resources: Any,
        timezone: str,
        now: datetime | None = None,
        chat: TickChat | None = None,
    ) -> None:
        self.hooks = hooks
        self.sessions = sessions
        self.chat = chat
        self.tz = ZoneInfo(timezone)
        self.schedule = TickSchedule(now=now or utcnow(), tz=self.tz)
        self.work = RunContext(
            resources=resources, still_current=lambda: True, publish=_no_chat, sessions=sessions
        )
        self.daily = tuple(
            spec.name
            for spec in hooks.specs
            if any(isinstance(on, OnTick) and on.at is not None for on in spec.on)
        )
        self.owed: list[Work] = []

    async def look(self, now: datetime | None = None) -> None:
        moment = now or utcnow()
        midnight = datetime.combine(moment.astimezone(self.tz).date(), time(0), tzinfo=self.tz)
        async with self.sessions() as session:
            if self.daily:
                await forget_before(session, self.daily, midnight)
                await session.commit()
            # Each reader once: hooks that share one share its Tick.
            clocks = {clock: await clock(session) for clock in self.hooks.daily_clocks}
        due = self.schedule.due(moment, clocks, self.hooks.intervals)
        work = self.work
        if due and self.chat is not None:
            work, due = await self._in_chat(due)
        for tick in due:
            jobs = await hand_on(self.hooks, self.sessions, tick, work=work)
            if tick.every is None:
                self.owed += jobs
            else:
                for job in jobs:
                    await job()
        self.owed = [job for job in self.owed if not await job()]

    async def _in_chat(self, due: list[Tick]) -> tuple[RunContext, list[Tick]]:
        """This look's work, able to speak until the owner acts, and its Ticks with the chat
        as the look saw it."""
        chat = self.chat
        assert chat is not None
        acted = chat.owner_acted_at()
        newest = await chat.newest()
        state = ChatState(acted, await chat.free(), *(newest or (None, None)))

        def still_current() -> bool:
            return chat.owner_acted_at() == acted

        async def publish(text: str, kind: str) -> None:
            await chat.speak(text, kind, still_current)

        work = replace(self.work, still_current=still_current, publish=publish)
        return work, [replace(tick, chat=state) for tick in due]


async def run_ticks(
    hooks: HookRegistry,
    sessions: async_sessionmaker[AsyncSession],
    *,
    resources: Any,
    timezone: str,
    poll_seconds: float,
    chat: TickChat | None = None,
) -> None:
    poll = TickPoll(hooks, sessions, resources=resources, timezone=timezone, chat=chat)
    await run_poll(poll.look, poll_seconds=poll_seconds, name="The hook tick poll")


class CommittedSink:
    """Where a session's committed facts go: one task per commit, kept until it is done."""

    def __init__(
        self,
        hooks: HookRegistry,
        sessions: async_sessionmaker[AsyncSession],
        *,
        resources: Any = None,
    ) -> None:
        self.hooks = hooks
        self.sessions = sessions
        self.work = RunContext(
            resources=resources, still_current=lambda: True, publish=_no_chat, sessions=sessions
        )
        self._tasks: set[asyncio.Task[None]] = set()
        # Commits are handed on in the order they happened: one hook, one row, no race
        # between two facts arriving together.
        self._in_order = asyncio.Lock()

    def publish(self, changes: Sequence[Committed]) -> None:
        if not changes or not self.hooks.listens(Committed):
            return
        task = asyncio.get_running_loop().create_task(self._deliver(changes))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for every commit published so far to reach its hooks."""
        while self._tasks:
            await asyncio.gather(*self._tasks)

    async def close(self) -> None:
        """Stop handing on: what a cancelled task had not written is one lost request."""
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _deliver(self, changes: Sequence[Committed]) -> None:
        jobs: list[Work] = []
        async with self._in_order:
            for change in changes:
                try:
                    jobs += await hand_on(self.hooks, self.sessions, change, work=self.work)
                except Exception:
                    logger.exception("A committed change did not reach its hooks: %s", change)
        # A hook's own work — a model call, say — takes its time after the order is kept.
        for job in jobs:
            await job()


def bind_committed(
    sessions: async_sessionmaker[AsyncSession], hooks: HookRegistry, *, resources: Any = None
) -> CommittedSink:
    """Make every session this factory opens hand its committed facts to these hooks;
    `resources` is what a Run hook on a commit reaches for."""
    sink = CommittedSink(hooks, sessions, resources=resources)
    sessions.configure(info={_SINK: sink})
    return sink


@event.listens_for(Session, "after_commit")
def _hand_on(session: Session) -> None:
    # Runs in the event loop's thread, inside the commit that just finished.
    sink = session.info.get(_SINK)
    changes = take_changes(session.info)
    if sink is not None:
        sink.publish(changes)


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    session.info.pop(CHANGES, None)
