"""What the bot keeps about a message, which is never the message.

The chat is the record of what was said. A note says only what a reader of the chat cannot
work out: which of the bot's messages is a screen the person can still act on, which item
that screen is about, and an identifier that survives editing it. Losing every note loses
no conversation.

The store is the host's, because the table belongs to the host's database. This is the
shape the package asks of it.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Note:
    """One remembered message, without its words."""

    chat_id: int
    message_id: int
    direction: str
    kind: str
    related_id: int | None = None
    event_id: str | None = None


class NoteStore(Protocol):
    """Where notes live. Each call owns its own transaction."""

    async def outgoing(
        self, chat_id: int, *, kinds: Collection[str] | None = None
    ) -> Sequence[Note]:
        """Every message the bot sent in this chat, newest first, of these kinds if named."""

    async def note(self, chat_id: int, message_id: int) -> Note | None:
        """What is remembered about one message, or None if nothing is."""

    async def write(self, note: Note) -> None:
        """Remember this message, replacing whatever was remembered about it.

        A note without an event identifier keeps the one already stored: a message can be
        redrawn many times and stays the same delivery.
        """

    async def forget(self, chat_id: int, message_id: int) -> None:
        """Drop the note for one message. A message with no note is not a screen."""
