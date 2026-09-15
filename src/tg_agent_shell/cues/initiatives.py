"""From a committed change to a hook's pending request.

An operation records what it changed beside its transaction (`foundation/changes.py`).
After the commit, the one listener below hands those facts to the hooks that subscribe to
that kind of change, and whatever an Advise check returns is merged into that hook's one
pending `Cue`. A rollback leaves nothing to hand on.

The facts are handed on outside the transaction that made them: a process that dies in
between loses one request, never the change itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from ..foundation.changes import CHANGES, Committed, take_changes
from ..hooks.contracts import Advise
from ..hooks.registry import HookEvent, HookRegistry
from .queue import merge_hook_cue

logger = logging.getLogger(__name__)

_SINK = "committed_sink"


async def queue_advice(
    hooks: HookRegistry, sessions: async_sessionmaker[AsyncSession], event: HookEvent
) -> None:
    """Check one event against the hooks and keep each Advise result as a pending request."""
    async for checked in hooks.evaluate(event, sessions):
        if checked.error is not None:
            logger.error("Hook %s failed: %s", checked.spec.name, checked.error)
            continue
        if not isinstance(checked.spec.effect, Advise) or not checked.payloads:
            continue
        async with sessions() as session:
            await merge_hook_cue(session, hook=checked.spec.name, items=checked.payloads)
            await session.commit()


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
