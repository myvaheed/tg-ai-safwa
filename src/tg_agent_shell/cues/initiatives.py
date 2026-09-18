"""From a committed change, or a time of day, to a hook's pending request or its work.

An operation records what it changed beside its transaction (`foundation/changes.py`).
After the commit, the one listener below hands those facts to the hooks that subscribe to
that kind of change, and whatever an Advise check returns is written down as one `Cue`
row of that hook. A rollback leaves nothing to hand on. The one tick poll hands a `Tick` to
the daily hooks when the time of day their reader names passes, by the same path. A Run
hook does its work there and then, with no chat to publish to: on a tick inside the poll,
on a commit once that commit's facts are handed on, outside the order they are kept in.

The facts are handed on outside the transaction that made them: a process that dies in
between loses one request, never the change itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
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
from ..hooks.contracts import Advise, OnTick, Run, RunContext
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


class TickPoll:
    """Hands the daily checks their Tick when their time comes: once, however many looks.

    A look also drops a daily hook's request the local midnight has passed since — the day
    it was about is over — and tries again the work an earlier look could not finish, until
    it is done: a Sprint's midnight end is not optional the way a request is.
    """

    def __init__(
        self,
        hooks: HookRegistry,
        sessions: async_sessionmaker[AsyncSession],
        *,
        resources: Any,
        timezone: str,
        now: datetime | None = None,
    ) -> None:
        self.hooks = hooks
        self.sessions = sessions
        self.tz = ZoneInfo(timezone)
        self.schedule = TickSchedule(now=now or utcnow(), tz=self.tz)
        self.work = RunContext(
            resources=resources, still_current=lambda: True, publish=_no_chat, sessions=sessions
        )
        self.daily = tuple(
            spec.name for spec in hooks.specs if any(isinstance(on, OnTick) for on in spec.on)
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
        for tick in self.schedule.due(moment, clocks):
            self.owed += await hand_on(self.hooks, self.sessions, tick, work=self.work)
        self.owed = [job for job in self.owed if not await job()]


async def run_ticks(
    hooks: HookRegistry,
    sessions: async_sessionmaker[AsyncSession],
    *,
    resources: Any,
    timezone: str,
    poll_seconds: float,
) -> None:
    poll = TickPoll(hooks, sessions, resources=resources, timezone=timezone)
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
