"""How much of the chat the model is given, and in what shape.

The chat is the conversation, and the bot keeps it as the messages pass through it. Before
every answer the window is read back out of what it kept: the newest messages that are
conversation at all, as far back as a token budget or the host's own edge allows, laid out
as turns rather than as messages.

What counts as conversation is the host's to say — which of its message kinds are the
person and which are the assistant — and it says it once, in a `ChatVocabulary`. Where the
window ends is the host's too, and it answers that per read, in a `WindowEdge`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from .notes import NoteStore
from .text import split_receipts, telegram_html_to_text

# A ceiling on how many messages one backwards scan may walk. The budget or a Summary
# normally stops it far sooner.
SCAN_LIMIT = 2_000


@dataclass(frozen=True, slots=True)
class DialogueMessage:
    """One turn of the conversation, as a model reads it."""

    role: str
    content: str


@dataclass(frozen=True)
class HistoryEntry:
    """One message the window kept, with what the host's kind made of it."""

    message_id: int
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
    """The conversation, read out of the kept chat every time it is asked for."""

    def __init__(
        self,
        notes: NoteStore,
        vocabulary: ChatVocabulary,
        *,
        count_tokens: Callable[[str], int],
        token_budget: int,
        edge: WindowEdge | None = None,
        edge_context_limit: int = 0,
        timezone: str = "UTC",
    ) -> None:
        self.notes = notes
        self.vocabulary = vocabulary
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
        budget = self.token_budget if token_budget is None else token_budget
        since = _aware(since) if since else None
        until = _aware(until) if until else None
        selected: list[HistoryEntry] = []
        before_edge: list[HistoryEntry] = []
        edge_parts: list[str] = []
        edge_seed: tuple[int, datetime, str] | None = None
        in_edge_run = False
        spent = 0
        for note in await self.notes.messages(chat_id, limit=SCAN_LIMIT):
            raw_text = telegram_html_to_text(
                note.text or "", self.vocabulary.citation_types
            ).strip()
            created_at = _aware(note.at) if note.at else datetime.now(UTC)
            if since is not None and created_at <= since:
                break
            if until is not None and created_at >= until:
                continue
            if not raw_text:
                continue
            kind = note.kind

            # A run of edge parts is broken by any message at all, not only by one that
            # is conversation: two edges with a screen between them are two edges.
            after_edge, in_edge_run = in_edge_run, False
            if (
                note.direction == "out"
                and self.edge is not None
                and self.edge.ends_window(note.message_id, kind, raw_text)
            ):
                if not stop_at_edge:
                    continue
                if not edge_parts:
                    edge_parts = [raw_text]
                    edge_seed = (note.message_id, created_at, kind)
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

            entry = HistoryEntry(
                message_id=note.message_id,
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
        return ([boundary] + before_edge + selected) if boundary else selected

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

    async def dialogue(self, chat_id: int) -> list[DialogueMessage]:
        """The window laid out as turns: everything that is not an answer gathers into one."""
        entries = await self.recent(chat_id)
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
        self, seed: tuple[int, datetime, str] | None, parts: Sequence[str]
    ) -> HistoryEntry | None:
        """The one entry the window opens with, in the host's own words."""
        if seed is None or self.edge is None:
            return None
        message_id, created_at, kind = seed
        return HistoryEntry(
            message_id=message_id,
            role="user",
            text=self.edge.stands_for(parts),
            created_at=created_at,
            kind=kind,
            edge=True,
        )

    def _content(self, entry: HistoryEntry, stamp: str | None) -> str:
        head = f"[{stamp}] " if stamp else ""
        # An assistant turn is already an assistant-role message; only the person's words
        # and the messages packed beside the edge need to say whose they are.
        if entry.before_edge or (entry.role == "user" and not entry.edge):
            return f"{head}[{entry.role.title()}]: {entry.text}"
        return f"{head}{entry.text}"


def _aware(moment: datetime) -> datetime:
    """A store may hand back naive datetimes; every comparison here is in UTC."""
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)
