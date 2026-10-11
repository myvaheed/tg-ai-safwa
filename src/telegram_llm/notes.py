"""What the bot keeps about each message of the chat: the record the window is read from.

A bot cannot read its own chat back, so it keeps the chat itself as the messages pass
through it: the words as they stand in the chat, what kind of message each one is, which
item a screen is about, and an identifier that survives editing it. Removing a screen drops
its note, or keeps it without words as a conversation boundary. A deletion by the person
is not seen, so that message's note stays.

The store is the host's, because the table belongs to the host's database. This is the
shape the package asks of it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Note:
    """One message as the bot keeps it."""

    chat_id: int
    message_id: int
    direction: str
    kind: str
    related_id: int | None = None
    event_id: str | None = None
    # The words this message carries, in Telegram HTML. None for one whose words are not
    # kept: a value typed into a field, or a later part of something Telegram needed
    # several messages for — the first part keeps all of it.
    text: str | None = None
    # When it was put in the chat. The first note written for a message stamps it, and a
    # rewrite never moves it: an answer drawn over a screen stands where the screen stood.
    at: datetime | None = None
    # What the model reads for this message, in the provider's shape, when it is not its
    # words alone: an answer's calls, their results and the model's own words, or the
    # person's words a relayed message carries without its heading.
    reads_as: tuple[Mapping[str, Any], ...] | None = None


class NoteStore(Protocol):
    """Where notes live. Each call owns its own transaction."""

    async def outgoing(
        self, chat_id: int, *, kinds: Collection[str] | None = None
    ) -> Sequence[Note]:
        """Every message the bot sent in this chat, newest first, of these kinds if named."""

    async def messages(self, chat_id: int, *, limit: int) -> Sequence[Note]:
        """The kept chat and its conversation boundaries, newest first."""

    async def note(self, chat_id: int, message_id: int) -> Note | None:
        """What is remembered about one message, or None if nothing is."""

    async def write(self, note: Note) -> None:
        """Remember this message, replacing whatever was remembered about it.

        A note without an event identifier keeps the one already stored: a message can be
        redrawn many times and stays the same delivery.
        """

    async def forget(self, chat_id: int, message_id: int) -> None:
        """Drop the note for one message: it is no longer in the chat."""
