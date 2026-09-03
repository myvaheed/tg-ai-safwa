"""How much of the chat the model is given, and in what shape.

The chat is the conversation. Before every answer the window is read back out of it: the
newest messages that are conversation at all, as far back as a token budget or the host's
own edge allows, laid out as turns rather than as messages.

What counts as conversation is the host's to say — which of its message kinds are the
person and which are the assistant — and it says it once, in a `ChatVocabulary`. Where the
window ends is the host's too, and it answers that per read, in a `WindowEdge`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .marking import KindMarks
from .notes import NoteStore
from .text import restore_citations, split_receipts

# A ceiling on how many messages one backwards scan may walk. The budget or a Summary
# normally stops it far sooner.
SCAN_LIMIT = 2_000


@dataclass(frozen=True, slots=True)
class DialogueMessage:
    """One turn of the conversation, as a model reads it."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message as the chat holds it: the words, who sent them, and when."""

    id: int
    text: str
    sender_id: int | None
    date: datetime
    entities: Sequence[Any] | None = None


@dataclass(frozen=True)
class HistoryEntry:
    """One message the window kept, with what the host's kind made of it."""

    message_id: int
    sender_id: int | None
    role: str
    text: str
    created_at: datetime
    kind: str
    # Older than the edge and kept anyway, as context for what the edge stands for.
    before_edge: bool = False
    # The edge itself. The host wrote this text and it is read exactly as written.
    edge: bool = False


@dataclass(frozen=True)
class ChatVocabulary:
    """What the host's message kinds mean to the conversation.

    Anything not named here is off the record: a screen, a receipt, a progress line. A kind
    that is `person` or is in `assistant` is something that was said. Where the window ends
    is not a kind and is not here: it is the `WindowEdge`, asked per read.
    """

    # What the person's words are, whoever put them in the chat: their own message, or the
    # bot relaying words that never reached it as theirs, such as a transcript.
    person: str
    assistant: frozenset[str]
    # Item types the bot cites, so a link it wrote reads back as the citation it wrote.
    citation_types: tuple[str, ...] = ()
    # Lines the interface writes into the bot's own messages, and what each one means.
    # Read back, they are the outcome of work rather than anything the bot said.
    receipts: Mapping[str, str] = field(default_factory=dict)


class ChatReader(Protocol):
    """Reading the real chat back, newest message first."""

    def messages(self, limit: int) -> AsyncIterator[ChatMessage]: ...


class WindowEdge(Protocol):
    """Where the window ends, and what the model reads in place of everything older.

    Asked of the bot's own messages, newest first, so the host answers out of whatever
    state it has and what ends the window can be a different thing on every turn. The
    parts handed to `stands_for` are one message too long for Telegram, oldest first, and
    what it returns is read as it is written — the host's own word for it included.
    """

    def ends_window(self, message_id: int, kind: str | None, text: str) -> bool: ...

    def stands_for(self, parts: Sequence[str]) -> str: ...


class ChatWindow:
    """The conversation, read out of the chat every time it is asked for."""

    def __init__(
        self,
        reader: ChatReader | None,
        notes: NoteStore,
        marks: KindMarks,
        vocabulary: ChatVocabulary,
        *,
        bot_user_id: int,
        owner_id: int,
        count_tokens: Callable[[str], int],
        token_budget: int,
        edge: WindowEdge | None = None,
        edge_context_limit: int = 0,
        timezone: str = "UTC",
    ) -> None:
        self.reader = reader
        self.notes = notes
        self.marks = marks
        self.vocabulary = vocabulary
        self.bot_user_id = bot_user_id
        self.owner_id = owner_id
        self.count_tokens = count_tokens
        self.token_budget = token_budget
        self.edge = edge
        self.edge_context_limit = edge_context_limit
        self.tz = ZoneInfo(timezone)

    async def recent(
        self,
        chat_id: int,
        *,
        token_budget: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        source_message: HistoryEntry | None = None,
        stop_at_edge: bool = True,
    ) -> list[HistoryEntry]:
        """The window: the host's edge plus as many messages as fit.

        The scan walks backwards and stops at the first of three edges — ``since``, the one
        the host names, or the token budget spent.  The budget is checked before an entry
        is taken, so the cut always lands between messages.  ``until`` skips everything
        newer instead of stopping, which is what closes a past day at both ends.

        ``stop_at_edge=False`` reads past the host's edge instead of stopping at it, for a
        caller that asked for a period rather than for a window: an edge written at noon
        must not cut that day in half.
        """
        if self.reader is None:
            return [source_message] if source_message else []
        budget = self.token_budget if token_budget is None else token_budget
        # The bot's own messages only, and matched on an event id it minted itself. Nothing
        # the person sent is looked up at all: the chat is the whole of the evidence.
        by_event = {
            note.event_id: note
            for note in await self.notes.outgoing(chat_id)
            if note.event_id
        }
        since = _aware(since) if since else None
        until = _aware(until) if until else None
        selected: list[HistoryEntry] = []
        before_edge: list[HistoryEntry] = []
        edge_parts: list[str] = []
        edge_seed: tuple[int, int | None, datetime, str | None] | None = None
        in_edge_run = False
        source_seen = False
        spent = 0
        async for message in self.reader.messages(SCAN_LIMIT):
            marked_kind, marked_event_id, raw_text = self.marks.read(
                restore_citations(
                    message.text, message.entities, self.vocabulary.citation_types
                ).strip()
            )
            raw_text = raw_text.strip()
            created_at = message.date.astimezone(UTC)
            if since is not None and created_at <= since:
                break
            if until is not None and created_at >= until:
                continue
            if not raw_text:
                continue
            sender_id = message.sender_id
            from_bot = sender_id == self.bot_user_id
            note = by_event.get(marked_event_id) if from_bot and marked_event_id else None
            kind = marked_kind or (note.kind if note is not None else None)
            if (
                source_message is not None
                and note is not None
                and note.message_id == source_message.message_id
            ):
                source_seen = True

            # A run of edge parts is broken by any message at all, not only by one that
            # is conversation: two edges with a screen between them are two edges.
            after_edge, in_edge_run = in_edge_run, False
            if from_bot:
                if self.edge is not None and self.edge.ends_window(message.id, kind, raw_text):
                    if not stop_at_edge:
                        continue
                    if not edge_parts:
                        edge_parts = [raw_text]
                        edge_seed = (message.id, sender_id, created_at, kind)
                    elif after_edge:
                        # One edge too long for a single Telegram message.  Its parts are
                        # consecutive, and the scan meets them newest first.
                        edge_parts.insert(0, raw_text)
                    # Anything older is already stood for by the nearest edge.
                    in_edge_run = True
                    continue
                if kind == self.vocabulary.person:
                    role = "user"
                elif kind in self.vocabulary.assistant:
                    role = "assistant"
                else:
                    continue
            elif sender_id == self.owner_id:
                # The person's client cannot carry a mark, so the chat itself is the
                # evidence: commands and typed field input are deleted from it, so
                # surviving text of theirs is conversation.  Text opening with "/" never
                # is, whether or not the deletion went through.
                if kind is None and not raw_text.startswith("/"):
                    kind = self.vocabulary.person
                if kind != self.vocabulary.person:
                    continue
                role = "user"
            else:
                continue

            entry = HistoryEntry(
                message_id=message.id,
                sender_id=sender_id,
                role=role,
                text=raw_text,
                created_at=created_at,
                kind=kind,
            )
            cost = self.count_tokens(entry.text)
            if (selected or before_edge) and spent + cost > budget:
                break
            spent += cost
            if edge_parts:
                if len(before_edge) >= self.edge_context_limit:
                    break
                before_edge.append(replace(entry, before_edge=True))
            else:
                selected.append(entry)

        selected.reverse()
        before_edge.reverse()
        boundary = self._boundary(edge_seed, edge_parts)
        result = ([boundary] + before_edge + selected) if boundary else selected
        if source_message and not source_seen and not _already_read(result, source_message):
            result.append(source_message)
        return result

    async def day_transcript(
        self, chat_id: int, *, start: datetime, end: datetime, token_budget: int
    ) -> str:
        """One period of conversation as plain text, oldest first, stamped to the minute.

        Read by a model that does not have the conversation, so every line says who spoke.
        """
        entries = await self.recent(
            chat_id,
            token_budget=token_budget,
            since=start,
            until=end,
            stop_at_edge=False,
        )
        return "\n".join(
            f"[{entry.created_at.astimezone(self.tz):%H:%M}] "
            f"[{entry.role.title()}]: {entry.text}"
            for entry in entries
        )

    async def dialogue(
        self, chat_id: int, *, source_message: HistoryEntry | None = None
    ) -> list[DialogueMessage]:
        """The window laid out as turns: everything that is not an answer gathers into one."""
        entries = await self.recent(chat_id, source_message=source_message)
        dialogue: list[DialogueMessage] = []
        pending_user: list[str] = []
        # One stamp per hour of conversation: a per-message one costs several percent of
        # the window and says nothing the previous line did not.
        stamped_hour: tuple[int, ...] | None = None

        def flush_user() -> None:
            if pending_user:
                dialogue.append(DialogueMessage(role="user", content="\n".join(pending_user)))
                pending_user.clear()

        for entry in entries:
            local = entry.created_at.astimezone(self.tz)
            hour = (local.year, local.month, local.day, local.hour)
            stamp = local.strftime("%Y-%m-%d %H:%M") if hour != stamped_hour else None
            stamped_hour = hour
            notes, spoken = (
                split_receipts(entry.text, self.vocabulary.receipts)
                if entry.role == "assistant"
                else ([], entry.text)
            )
            # A tool result belongs to this turn but not to the bot's voice, so it leads
            # the owner block that the answer replies to.
            for line in notes:
                pending_user.append(f"[{stamp}] {line}" if stamp else line)
                stamp = None
            if not spoken:
                continue
            content = self._content(replace(entry, text=spoken), stamp)
            if entry.role == "assistant" and not entry.before_edge:
                flush_user()
                if dialogue and dialogue[-1].role == "assistant":
                    dialogue[-1] = replace(
                        dialogue[-1], content=dialogue[-1].content + "\n" + content
                    )
                else:
                    dialogue.append(DialogueMessage(role="assistant", content=content))
                continue
            pending_user.append(content)
        flush_user()
        return dialogue

    def _boundary(
        self,
        seed: tuple[int, int | None, datetime, str | None] | None,
        parts: Sequence[str],
    ) -> HistoryEntry | None:
        """The one entry the window opens with, in the host's own words."""
        if seed is None or self.edge is None:
            return None
        message_id, sender_id, created_at, kind = seed
        return HistoryEntry(
            message_id=message_id,
            sender_id=sender_id,
            role="user",
            text=self.edge.stands_for(parts),
            created_at=created_at,
            kind=kind or "",
            edge=True,
        )

    def _content(self, entry: HistoryEntry, stamp: str | None) -> str:
        head = f"[{stamp}] " if stamp else ""
        # An assistant turn is already an assistant-role message; only the person's words
        # and the messages packed beside the edge need to say whose they are.
        if entry.before_edge or (entry.role == "user" and not entry.edge):
            return f"{head}[{entry.role.title()}]: {entry.text}"
        return f"{head}{entry.text}"


def _already_read(entries: list[HistoryEntry], source: HistoryEntry) -> bool:
    """Whether the chat read already came back with the message being answered.

    Words the bot posted on the person's behalf are recognised by the event id on its own
    note.  The person's own typed message carries no note, and a user session numbers it
    differently from the Bot API, so the words and the minute are what identify it.
    """
    minute = source.created_at.replace(second=0, microsecond=0)
    return any(
        entry.text == source.text
        and entry.created_at.replace(second=0, microsecond=0) == minute
        for entry in entries
    )


def _aware(moment: datetime) -> datetime:
    """A store may hand back naive datetimes; every comparison here is in UTC."""
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)
