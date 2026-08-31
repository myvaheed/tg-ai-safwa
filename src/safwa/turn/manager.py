"""One turn at a time, and who is holding it.

The turn's identity is the Telegram message id being answered. It lives in memory only:
a restart ends every turn, which is what `AG-SESSION-009` already says.

Holding the turn is what makes the middleware reject callbacks and take the owner's other
messages out of the chat, so only one answer is ever being written. `dialogue_revision` is
not part of the state: it is an invalidation token for a request already sent to the
provider, bumped by :meth:`cancel`, and an answer that comes back against an old one is
thrown away.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, TypeVar

from .model import Answering, BackgroundWork, Idle, TurnState

R = TypeVar("R")


def _current_task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class TurnManager:
    """The single foreground/background lease, and the only writer of `TurnState`."""

    def __init__(self) -> None:
        self._state: TurnState = Idle()
        self.dialogue_revision = 0

    @property
    def state(self) -> TurnState:
        return self._state

    @property
    def active(self) -> bool:
        return not isinstance(self._state, Idle)

    @property
    def background(self) -> bool:
        return isinstance(self._state, BackgroundWork)

    @property
    def source_message_id(self) -> int | None:
        return self._state.source_message_id if isinstance(self._state, Answering) else None

    def begin(self, source_message_id: int) -> None:
        """Take the turn for one owner message, or refuse if another already holds it."""
        if isinstance(self._state, Answering) and (
            self._state.source_message_id != source_message_id
        ):
            raise RuntimeError("Another answer is already being written")
        if isinstance(self._state, BackgroundWork):
            raise RuntimeError("Background work still holds the turn")
        self._state = Answering(source_message_id, task=_current_task())

    def try_begin(self, source_message_id: int) -> bool:
        """Take the turn if it is free, and say whether this caller now holds it."""
        if isinstance(self._state, Answering):
            return self._state.source_message_id == source_message_id
        if isinstance(self._state, BackgroundWork):
            return False
        self._state = Answering(source_message_id, task=_current_task())
        return True

    def try_begin_background(self) -> bool:
        """Take the turn for work nobody asked for, or decline if it is held.

        Never takes it from the owner; the caller comes back later.
        """
        if not isinstance(self._state, Idle):
            return False
        self._state = BackgroundWork()
        return True

    def notice_shown(self, source_message_id: int, notice_message_id: int) -> None:
        """Record the message standing in the chat while this answer is written."""
        if isinstance(self._state, Answering) and (
            self._state.source_message_id == source_message_id
        ):
            self._state = replace(self._state, notice=notice_message_id)

    def end(self, source_message_id: int | None = None) -> int | None:
        """Give the turn back, and hand over the notice still standing in the chat.

        A source id that is not the one holding the turn ends nothing: a `finally` that
        runs after the turn already changed hands must not take it from its new holder.
        """
        state = self._state
        if source_message_id is not None and (
            not isinstance(state, Answering) or state.source_message_id != source_message_id
        ):
            return None
        self._state = Idle()
        return state.notice if isinstance(state, Answering) else None

    def end_background(self) -> None:
        """Give back a turn taken for work nobody asked for, and only that turn.

        The owner may have taken it in the meantime, and background work that finishes
        afterwards must not take it from them.
        """
        if self.background:
            self._state = Idle()

    def cancel(self) -> int | None:
        """Stop whatever is being written, and hand over its notice to be taken back."""
        state = self._state
        self.dialogue_revision += 1
        self._state = Idle()
        if not isinstance(state, Answering):
            return None
        task = state.task
        if task is not None and task is not _current_task() and not task.done():
            task.cancel()
        return state.notice

    async def run_background(
        self, operation: Callable[[Callable[[], bool]], Awaitable[R]]
    ) -> R | None:
        """Run one background operation while its lease is still the current one.

        The owner always wins: a held turn postpones the operation rather than queueing
        it. The callback receives the single staleness predicate Summary and memory read.
        """
        if not self.try_begin_background():
            return None
        revision = self.dialogue_revision

        def still_current() -> bool:
            return self.background and self.dialogue_revision == revision

        try:
            return await operation(still_current)
        finally:
            self.end_background()
