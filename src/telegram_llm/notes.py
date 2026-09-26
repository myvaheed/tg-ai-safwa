"""What the bot keeps about each message of the chat: the record the window is read from.

A bot cannot read its own chat back, so it keeps the chat itself as the messages pass
through it: the words as they stand in the chat, what kind of message each one is, which
item a screen is about, and an identifier that survives editing it. A message the bot takes
out of the chat takes its note with it; one the person deletes is not seen, and stays.

The store is the host's, because the table belongs to the host's database. This is the
shape the package asks of it.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Note:
    """One message as the bot keeps it."""

    chat_id: int
    message_id: int
    direction: str
    kind: str
    related_id: int | None = None
    event_id: str | None = None
    # The message as it stands in the chat, in Telegram HTML. None for one the person sent
    # that is not conversation — a value typed into a field — whose words are not kept.
    text: str | None = None
    # When it was put in the chat. The first note written for a message stamps it, and a
    # rewrite never moves it: an answer drawn over a screen stands where the screen stood.
    at: datetime | None = None


class NoteStore(Protocol):
    """Where notes live. Each call owns its own transaction."""

    async def outgoing(
        self, chat_id: int, *, kinds: Collection[str] | None = None
    ) -> Sequence[Note]:
        """Every message the bot sent in this chat, newest first, of these kinds if named."""

    async def messages(self, chat_id: int, *, limit: int) -> Sequence[Note]:
        """The chat as it stands: every message whose words are kept, newest first."""

    async def note(self, chat_id: int, message_id: int) -> Note | None:
        """What is remembered about one message, or None if nothing is."""

    async def write(self, note: Note) -> None:
        """Remember this message, replacing whatever was remembered about it.

        A note without an event identifier keeps the one already stored: a message can be
        redrawn many times and stays the same delivery.
        """

    async def forget(self, chat_id: int, message_id: int) -> None:
        """Drop the note for one message: it is no longer in the chat."""
