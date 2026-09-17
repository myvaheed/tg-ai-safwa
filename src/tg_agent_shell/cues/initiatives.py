"""From a committed change, or a time of day, to a hook's pending request or its work.

An operation records what it changed beside its transaction (`foundation/changes.py`).
After the commit, the one listener below hands those facts to the hooks that subscribe to
that kind of change, and whatever an Advise check returns is merged into that hook's one
pending `Cue`. A rollback leaves nothing to hand on. The one tick poll hands a `Tick` to
the daily hooks when the time of day their reader names passes, by the same path; a Run
hook on a tick does its work there and then, with no chat to publish to.

The facts are handed on outside the transaction that made them: a process that dies in
between loses one request, never the change itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from ..foundation.changes import CHANGES, Committed, take_changes
from ..foundation.clock import utcnow
from ..foundation.poll import run_poll
from ..hooks.contracts import Advise, Run, RunContext
from ..hooks.registry import HookEvent, HookRegistry
from ..hooks.ticks import TickSchedule
from .queue import merge_hook_cue

logger = logging.getLogger(__name__)

_SINK = "committed_sink"


async def queue_advice(
    hooks: HookRegistry,
    sessions: async_sessionmaker[AsyncSession],
    event: HookEvent,
    *,
    work: RunContext | None = None,
) -> None:
    """Check one event against the hooks: keep each Advise result as a pending request, and
    do each Run's work with `work` — which the adapter brings wherever the registry admits
    a Run."""
    async for checked in hooks.evaluate(event, sessions):
        if checked.error is not None:
            logger.error("Hook %s failed: %s", checked.spec.name, checked.error)
            continue
        if not checked.payloads:
            continue
        match checked.spec.effect:
            case Advise():
                async with sessions() as session:
                    await merge_hook_cue(session, hook=checked.spec.name, items=checked.payloads)
                    await session.commit()
            case Run(run=run):
                assert work is not None  # the registry admits a Run only where the adapter brings its context
                for payload in checked.payloads:
                    try:
                        await run(payload, work)
                    except Exception:
                        logger.exception("The work of the hook %s failed", checked.spec.name)


async def _no_chat(text: str, kind: str) -> None:
    raise RuntimeError("A hook on a tick has no chat to publish to: record a change, and let an Advise hook say it")


async def run_ticks(
    hooks: HookRegistry,
    sessions: async_sessionmaker[AsyncSession],
    *,
    resources: Any,
    timezone: str,
    poll_seconds: float,
) -> None:
    """Hand the daily checks their Tick when their time comes: once, however many polls."""
    schedule = TickSchedule(now=utcnow(), tz=ZoneInfo(timezone))
    work = RunContext(
        resources=resources, still_current=lambda: True, publish=_no_chat, sessions=sessions
    )

    async def look() -> None:
        async with sessions() as session:
            # Each reader once: hooks that share one share its Tick.
            clocks = {clock: await clock(session) for clock in hooks.daily_clocks}
        for tick in schedule.due(utcnow(), clocks):
            await queue_advice(hooks, sessions, tick, work=work)

    await run_poll(look, poll_seconds=poll_seconds, name="The hook tick poll")


class CommittedSink:
    """Where a session's committed facts go: one task per commit, kept until it is done."""

    def __init__(self, hooks: HookRegistry, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.hooks = hooks
        self.sessions = sessions
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
        async with self._in_order:
            for change in changes:
                try:
                    await queue_advice(self.hooks, self.sessions, change)
                except Exception:
                    logger.exception("A committed change did not reach its hooks: %s", change)


def bind_committed(
    sessions: async_sessionmaker[AsyncSession], hooks: HookRegistry
) -> CommittedSink:
    """Make every session this factory opens hand its committed facts to these hooks."""
    sink = CommittedSink(hooks, sessions)
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
