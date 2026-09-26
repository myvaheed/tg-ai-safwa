"""How much of the chat the model is given, and in what shape.

The chat is the conversation, and the bot keeps it as the messages pass through it. Before
every answer the window is read back out of what it kept: the newest messages that are
conversation at all, as far back as a token budget or the host's own edge allows, laid out
the way a model was trained to read a conversation — an answer as its calls, their results
and its words, and the person's side as one turn up to the next answer.

What counts as conversation is the host's to say — which of its message kinds are the
person, which are the assistant, and which are events the interface reports — and it says
it once, in a `ChatVocabulary`. Where the window ends is the host's too, and it answers that
per read, in a `WindowEdge`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .notes import NoteStore
from .text import telegram_html_to_text

# A ceiling on how many messages one backwards scan may walk. The budget or a Summary
# normally stops it far sooner.
SCAN_LIMIT = 2_000

# How an event reads on the person's side: the interface speaking, not the person.
EVENT_LABEL = "[System]:"


@dataclass(frozen=True, slots=True)
class DialogueMessage:
    """One message of the conversation, as a model reads it."""

    role: str
    content: str | None
    # An assistant's calls, and the call a tool result answers, in the provider's shape.
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def of(cls, message: Mapping[str, Any]) -> DialogueMessage:
        return cls(
            role=str(message["role"]),
            content=message.get("content"),
            tool_calls=tuple(message.get("tool_calls") or ()),
            tool_call_id=message.get("tool_call_id"),
            name=message.get("name"),
        )

    def as_message(self) -> dict[str, Any]:
        """The provider's shape, with nothing the message does not carry."""
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [dict(call) for call in self.tool_calls]
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            message["name"] = self.name
        return message


@dataclass(frozen=True)
class HistoryEntry:
    """One message the window kept, with what the host's kind made of it."""

    message_id: int
    # `user`, `assistant`, or `system` for an event the interface reported.
    role: str
    # The words: what a reader that takes no part in the conversation is given.
    text: str
    created_at: datetime
    kind: str
    # Older than the edge and kept anyway, as context for what the edge stands for.
    before_edge: bool = False
    # The edge itself. The host wrote this text and it is read exactly as written.
    edge: bool = False
    # What the model reads for this message when it is not `text` alone, in the provider's
    # shape: an answer's calls and their results before its words.
    turn: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class ChatVocabulary:
    """What the host's message kinds mean to the conversation.

    Anything not named here is off the record: a screen, a receipt, a progress line. A kind
    that is `person` or is in `assistant` is something that was said; one in `events` is the
    interface reporting what happened, read on the person's side. Where the window ends is
    not a kind and is not here: it is the `WindowEdge`, asked per read.
    """

    # What the person's words are, whoever put them in the chat: their own message, or the
    # bot relaying words that never reached it as theirs, such as a transcript.
    person: str
    assistant: frozenset[str]
    events: frozenset[str] = frozenset()
    # Item types the bot cites, so a link it wrote reads back as the citation it wrote.
    citation_types: tuple[str, ...] = ()


class WindowEdge(Protocol):
    """Where the window ends, and what the model reads in place of everything older.

    Asked of the bot's own messages, newest first, so the host answers out of whatever
    state it has and what ends the window can be a different thing on every turn. What
    `stands_for` returns is read as it is written — the host's own word for it included.
    """

    def ends_window(self, message_id: int, kind: str | None, text: str) -> bool: ...

    def stands_for(self, text: str) -> str: ...


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
        the host names, or the token budget spent.  The budget is checked before a message
        is taken, and an answer is taken with its calls or not at all, so the cut always
        lands between messages and never between a call and its result.  ``until`` skips
        everything newer instead of stopping, which is what closes a past day at both ends.

        ``stop_at_edge=False`` reads past the host's edge instead of stopping at it, for a
        caller that asked for a period rather than for a window: an edge written at noon
        must not cut that day in half.
        """
        budget = self.token_budget if token_budget is None else token_budget
        since = _aware(since) if since else None
        until = _aware(until) if until else None
        selected: list[HistoryEntry] = []
        before_edge: list[HistoryEntry] = []
        boundary: HistoryEntry | None = None
        spent = 0
        for note in await self.notes.messages(chat_id, limit=SCAN_LIMIT):
            created_at = _aware(note.at) if note.at else datetime.now(UTC)
            if since is not None and created_at <= since:
                break
            if until is not None and created_at >= until:
                continue
            turn = tuple(note.reads_as or ())
            text = (
                _words(turn)
                if turn
                else telegram_html_to_text(note.text or "", self.vocabulary.citation_types)
            ).strip()
            if not text and not turn:
                continue
            if (
                note.direction == "out"
                and self.edge is not None
                and self.edge.ends_window(note.message_id, note.kind, text)
            ):
                if not stop_at_edge:
                    continue
                # Anything older is already stood for by the nearest edge, an older one too.
                if boundary is None:
                    boundary = HistoryEntry(
                        message_id=note.message_id,
                        role="user",
                        text=self.edge.stands_for(text),
                        created_at=created_at,
                        kind=note.kind,
                        edge=True,
                    )
                continue
            role = self._role(note.kind)
            if role is None:
                continue
            entry = HistoryEntry(
                message_id=note.message_id,
                role=role,
                text=text,
                created_at=created_at,
                kind=note.kind,
                # Beside the edge, a message is context for what the edge stands for, and
                # its words are all of that.
                turn=() if boundary is not None else turn,
            )
            cost = self.count_tokens(_turn_text(entry.turn) if entry.turn else entry.text)
            if (selected or before_edge) and spent + cost > budget:
                break
            spent += cost
            if boundary is not None:
                if len(before_edge) >= self.edge_context_limit:
                    break
                before_edge.append(replace(entry, before_edge=True))
            else:
                selected.append(entry)

        selected.reverse()
        before_edge.reverse()
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
        """The window laid out as the messages a model reads.

        An answer is its calls and their results, then its words, all in the assistant's
        own roles. Everything on the person's side — their words, an event, the edge —
        gathers into one user message up to the next answer.
        """
        entries = await self.recent(chat_id)
        dialogue: list[DialogueMessage] = []
        pending: list[str] = []
        # One stamp per hour of conversation, on the person's side only: a per-message one
        # costs several percent of the window, and one on an answer is an example of
        # answering with a timestamp.
        stamped_hour: tuple[int, ...] | None = None

        def person(text: str, at: datetime) -> None:
            nonlocal stamped_hour
            local = at.astimezone(self.tz)
            hour = (local.year, local.month, local.day, local.hour)
            head = f"[{local:%Y-%m-%d %H:%M}] " if hour != stamped_hour else ""
            stamped_hour = hour
            pending.append(head + text)

        def flush() -> None:
            if pending:
                dialogue.append(DialogueMessage(role="user", content="\n".join(pending)))
                pending.clear()

        for entry in entries:
            if entry.edge:
                person(entry.text, entry.created_at)
            elif entry.before_edge:
                # Packed into the person's turn beside the edge, so each says whose it is.
                person(f"[{entry.role.title()}]: {entry.text}", entry.created_at)
            elif entry.role == "system":
                person(f"{EVENT_LABEL} {entry.text}", entry.created_at)
            elif entry.role == "user" and not entry.turn:
                person(entry.text, entry.created_at)
            else:
                for message in entry.turn or ({"role": entry.role, "content": entry.text},):
                    if message.get("role") == "user":
                        person(str(message.get("content") or ""), entry.created_at)
                        continue
                    flush()
                    dialogue.append(DialogueMessage.of(message))
        flush()
        return dialogue

    def _role(self, kind: str) -> str | None:
        if kind == self.vocabulary.person:
            return "user"
        if kind in self.vocabulary.assistant:
            return "assistant"
        if kind in self.vocabulary.events:
            return "system"
        return None


def _words(turn: Sequence[Mapping[str, Any]]) -> str:
    """What a turn said in the end: its last message, unless that is a tool's result."""
    last = turn[-1]
    content = last.get("content")
    return content if last.get("role") != "tool" and isinstance(content, str) else ""


def _turn_text(turn: Sequence[Mapping[str, Any]]) -> str:
    """Everything a turn puts in front of the model, for counting what it costs."""
    parts: list[str] = []
    for message in turn:
        if isinstance(message.get("content"), str):
            parts.append(message["content"])
        for call in message.get("tool_calls") or ():
            function = call.get("function") or {}
            parts.append(f"{function.get('name', '')} {function.get('arguments', '')}")
    return "\n".join(parts)


def _aware(moment: datetime) -> datetime:
    """A store may hand back naive datetimes; every comparison here is in UTC."""
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)
