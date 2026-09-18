"""One message a long job is watched on: a bar, a percent, and what it is doing now.

It is a `STATUS` message, so it never becomes dialogue, and it is created on the first
report rather than up front: a job that is done before its first report shows nothing.
Telegram refuses an edit that changes nothing, so a report that renders the same text
sends nothing.
"""

from __future__ import annotations

import html

from aiogram.types import Message

from ..foundation.kinds import MessageKind
from .chat import delete_screen, edit_registered_message, send_registered
from .services import Services

PROGRESS_CELLS = 10


def progress_bar(done: float, total: float) -> str:
    """Ten cells and the whole percent, for any pair of numbers."""
    share = min(max(done / total, 0.0), 1.0) if total else 0.0
    # Never a cell ahead of the work: the last one fills with the last report.
    filled = int(share * PROGRESS_CELLS)
    return f"{'▓' * filled}{'░' * (PROGRESS_CELLS - filled)} {int(share * 100)}%"


class Progress:
    """The one message a job edits as it goes, and deletes when it ends."""

    def __init__(self, message: Message, services: Services, title: str) -> None:
        self.message = message
        self.services = services
        self.title = title
        self.message_id: int | None = None
        self._text = ""

    async def report(self, done: float, total: float, note: str = "") -> None:
        text = f"{html.escape(self.title)}\n{progress_bar(done, total)}"
        if note:
            text += f" · {html.escape(note)}"
        if text == self._text:
            return
        self._text = text
        if self.message_id is None:
            sent = await send_registered(
                self.message, self.services, text, kind=MessageKind.STATUS, replace=False
            )
            self.message_id = sent.message_id
            return
        await edit_registered_message(
            self.message, self.services, self.message_id, text, kind=MessageKind.STATUS
        )

    async def clear(self) -> None:
        if self.message_id is None:
            return
        await delete_screen(self.message, self.services, self.message_id)
        self.message_id = None
