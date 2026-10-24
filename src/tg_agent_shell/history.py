"""The bot's chat: the table it is kept in, and what its kinds mean.

The window itself is `telegram_llm`. This is what it has to be told — which message kinds
are the conversation and what each cites — plus what only a running bot can supply: the
`telegram_messages` table every message is kept in as it passes, the message the
conversation starts after, which the last clear set, and the stretches of the chat a
subsession holds instead. Where the window ends arrives the same way, as a `WindowEdge`.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    ColumnElement,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    cast,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from telegram_llm import ChatVocabulary, ChatWindow, Note, WindowEdge

from .foundation.clock import utcnow
from .foundation.kinds import MessageKind
from .foundation.models import Base, UtcDateTime

__all__ = [
    "CONVERSATION_KINDS",
    "ChatClearBoundary",
    "Subsession",
    "TelegramHistorySource",
    "TelegramMessage",
    "TelegramNotes",
    "cleared_through",
    "close_subsession",
    "current_subsession",
    "mark_cleared",
    "register_message",
    "result_event",
    "start_subsession",
    "undelivered_subsessions",
]

EDGE_CONTEXT_MESSAGE_LIMIT = 20

# What was said, in every kind it is kept under: what a clear keeps of the chat, because a
# period is still read after it left the chat. A passing Cue is not kept: its note leaves
# with it.
CONVERSATION_KINDS = frozenset(
    {
        MessageKind.DIALOGUE_USER.value,
        MessageKind.DIALOGUE_ASSISTANT.value,
        MessageKind.CUE.value,
        MessageKind.EVENT.value,
        MessageKind.SUMMARY.value,
    }
)


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(40), default=MessageKind.DASHBOARD.value)
    related_id: Mapped[int | None] = mapped_column(Integer)
    # The words it carries, in Telegram HTML; None when its words are not kept.
    text: Mapped[str | None] = mapped_column(Text)
    # What the model reads for it, in the provider's shape, when that is not its words.
    reads_as: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # The start of the message a reply of the person's answers.
    quote: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("chat_id", "message_id"),)


class ChatClearBoundary(Base):
    """The message the conversation starts after: the last clear's, before what it left."""

    __tablename__ = "chat_clear_boundaries"
    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    through_message_id: Mapped[int] = mapped_column(Integer)


async def cleared_through(session: AsyncSession, chat_id: int) -> int:
    """The message the conversation starts after, or 0 when the chat was never cleared."""
    boundary = await session.get(ChatClearBoundary, chat_id)
    return boundary.through_message_id if boundary is not None else 0


async def mark_cleared(session: AsyncSession, chat_id: int, through: int) -> None:
    boundary = await session.get(ChatClearBoundary, chat_id)
    if boundary is None:
        session.add(ChatClearBoundary(chat_id=chat_id, through_message_id=through))
    else:
        boundary.through_message_id = through


class Subsession(Base):
    """A stretch of the chat that is a conversation of its own, with another agent.

    A private chat numbers both sides' messages in one sequence, so the stretch is every
    message after `after_message_id`, through `through_message_id` once it is closed. A
    message belongs to it by its id alone: nothing is written on the message to say so.
    """

    __tablename__ = "subsessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer)
    # The agent that answers in it.
    kind: Mapped[str] = mapped_column(String(30))
    after_message_id: Mapped[int] = mapped_column(Integer)
    # None while it is open.
    through_message_id: Mapped[int | None] = mapped_column(Integer)
    # What it hands back to the conversation once it is closed.
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # A chat has one open at a time, and the database is what refuses a second.
    __table_args__ = (
        Index(
            "ix_subsessions_open",
            "chat_id",
            unique=True,
            sqlite_where=text("through_message_id IS NULL"),
        ),
    )


# What the conversation reads: no message any subsession's stretch holds. It is a condition of
# the query, so it applies before the scan's limit and the token budget.
OUTSIDE_SUBSESSIONS: ColumnElement[bool] = ~exists().where(
    Subsession.chat_id == TelegramMessage.chat_id,
    TelegramMessage.message_id > Subsession.after_message_id,
    or_(
        Subsession.through_message_id.is_(None),
        TelegramMessage.message_id <= Subsession.through_message_id,
    ),
)
# What the open subsession reads: its own stretch, whatever a clear did after it opened.
INSIDE_OPEN_SUBSESSION: ColumnElement[bool] = exists().where(
    Subsession.chat_id == TelegramMessage.chat_id,
    Subsession.through_message_id.is_(None),
    TelegramMessage.message_id > Subsession.after_message_id,
)


async def _newest_kept(session: AsyncSession, chat_id: int) -> int:
    newest = await session.scalar(
        select(func.max(TelegramMessage.message_id)).where(TelegramMessage.chat_id == chat_id)
    )
    return newest or 0


async def current_subsession(session: AsyncSession, chat_id: int) -> Subsession | None:
    """The subsession open in this chat, if one is."""
    return await session.scalar(
        select(Subsession).where(
            Subsession.chat_id == chat_id, Subsession.through_message_id.is_(None)
        )
    )


async def start_subsession(session: AsyncSession, chat_id: int, kind: str) -> Subsession:
    """Open a subsession after the newest message kept in this chat. The caller commits,
    and the commit is refused while another one is open."""
    subsession = Subsession(
        chat_id=chat_id, kind=kind, after_message_id=await _newest_kept(session, chat_id)
    )
    session.add(subsession)
    await session.flush()
    return subsession


# A closed subsession's result has reached the chat once a message carries this event id, as
# a Cue's answer has: the delivery is not recorded anywhere else.
RESULT_EVENT = "subsession:"


def result_event(subsession_id: int) -> str:
    return f"{RESULT_EVENT}{subsession_id}"


async def undelivered_subsessions(session: AsyncSession, chat_id: int) -> list[Subsession]:
    """The closed subsessions of this chat whose result never reached it, oldest first."""
    delivered = exists().where(
        TelegramMessage.chat_id == Subsession.chat_id,
        TelegramMessage.event_id == literal(RESULT_EVENT) + cast(Subsession.id, String),
    )
    return list(
        await session.scalars(
            select(Subsession)
            .where(
                Subsession.chat_id == chat_id,
                Subsession.through_message_id.is_not(None),
                ~delivered,
            )
            .order_by(Subsession.id)
        )
    )


async def close_subsession(session: AsyncSession, subsession_id: int, result: str) -> bool:
    """Close it through the newest message kept in its chat, with what it hands back.

    False when it was already closed: of two closings, the first decides. The caller commits.
    """
    subsession = await session.get(Subsession, subsession_id)
    if subsession is None:
        return False
    through = max(await _newest_kept(session, subsession.chat_id), subsession.after_message_id)
    closed = await session.scalar(
        update(Subsession)
        .where(Subsession.id == subsession_id, Subsession.through_message_id.is_(None))
        .values(through_message_id=through, result=result, closed_at=utcnow())
        .returning(Subsession.id)
    )
    return closed is not None


def vocabulary(citation_types: tuple[str, ...]) -> ChatVocabulary:
    """How the reader reads the chat back. The citation types are the features' own."""
    return ChatVocabulary(
        person=MessageKind.DIALOGUE_USER.value,
        assistant=frozenset(
            {
                MessageKind.DIALOGUE_ASSISTANT.value,
                MessageKind.CUE.value,
                MessageKind.PASSING_CUE.value,
            }
        ),
        events=frozenset({MessageKind.EVENT.value}),
        citation_types=citation_types,
    )


def _note(row: TelegramMessage) -> Note:
    return Note(
        chat_id=row.chat_id,
        message_id=row.message_id,
        direction=row.direction,
        kind=row.kind,
        related_id=row.related_id,
        event_id=row.event_id,
        text=row.text,
        at=row.created_at,
        reads_as=tuple(row.reads_as) if row.reads_as is not None else None,
        quote=row.quote,
    )


class TelegramNotes:
    """The window's `NoteStore`, over `telegram_messages`.

    `within` narrows what `messages` lists, which is all a window reads; the host's own
    store lists every message, because it takes back what a subsession left in the chat too.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        within: ColumnElement[bool] | None = None,
    ) -> None:
        self.sessions = sessions
        self.within = within

    async def outgoing(
        self, chat_id: int, *, kinds: Collection[str] | None = None
    ) -> Sequence[Note]:
        query = select(TelegramMessage).where(
            TelegramMessage.chat_id == chat_id,
            TelegramMessage.direction == "out",
        )
        if kinds is not None:
            query = query.where(TelegramMessage.kind.in_(kinds))
        async with self.sessions() as session:
            rows = list(await session.scalars(query.order_by(TelegramMessage.message_id.desc())))
        return [_note(row) for row in rows]

    async def messages(self, chat_id: int, *, limit: int) -> Sequence[Note]:
        # In a private chat Telegram numbers both sides' messages in one sequence, so the
        # message id is the order of the chat itself.
        query = select(TelegramMessage).where(TelegramMessage.chat_id == chat_id)
        if self.within is not None:
            query = query.where(self.within)
        async with self.sessions() as session:
            rows = list(
                await session.scalars(
                    query.order_by(TelegramMessage.message_id.desc()).limit(limit)
                )
            )
        return [_note(row) for row in rows]

    async def note(self, chat_id: int, message_id: int) -> Note | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(TelegramMessage).where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.message_id == message_id,
                )
            )
        return _note(row) if row is not None else None

    async def write(self, note: Note) -> None:
        async with self.sessions() as session:
            await register_message(
                session,
                note.chat_id,
                note.message_id,
                note.direction,
                MessageKind(note.kind),
                note.related_id,
                note.event_id,
                text=note.text,
                at=note.at,
                reads_as=note.reads_as,
                quote=note.quote,
            )
            await session.commit()

    async def forget(self, chat_id: int, message_id: int) -> None:
        async with self.sessions() as session:
            await session.execute(
                delete(TelegramMessage).where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.message_id == message_id,
                )
            )
            await session.commit()


async def register_message(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    direction: str,
    kind: MessageKind,
    related_id: int | None = None,
    event_id: str | None = None,
    *,
    text: str | None = None,
    at: datetime | None = None,
    reads_as: Sequence[Mapping[str, Any]] | None = None,
    quote: str | None = None,
) -> None:
    kept = [dict(message) for message in reads_as] if reads_as is not None else None
    existing = await session.scalar(
        select(TelegramMessage).where(
            TelegramMessage.chat_id == chat_id,
            TelegramMessage.message_id == message_id,
        )
    )
    if existing:
        existing.kind = kind.value
        existing.related_id = related_id
        existing.text = text
        existing.reads_as = kept
        existing.quote = quote
        if event_id is not None:
            existing.event_id = event_id
    else:
        row = TelegramMessage(
            chat_id=chat_id,
            message_id=message_id,
            event_id=event_id,
            direction=direction,
            kind=kind.value,
            related_id=related_id,
            text=text,
            reads_as=kept,
            quote=quote,
        )
        if at is not None:
            row.created_at = at
        session.add(row)


class TelegramHistorySource(ChatWindow):
    """The window over the conversation as the bot keeps it in `telegram_messages`.

    No subsession's stretch is in it. `subsession` is the window over the open one's stretch,
    read the same way and under the same budget.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        count_tokens: Callable[[str], int],
        token_budget: int,
        edge: WindowEdge,
        citation_types: tuple[str, ...] = (),
        timezone: str = "UTC",
    ) -> None:
        super().__init__(
            TelegramNotes(sessions, within=OUTSIDE_SUBSESSIONS),
            vocabulary(citation_types),
            count_tokens=count_tokens,
            token_budget=token_budget,
            edge=edge,
            edge_context_limit=EDGE_CONTEXT_MESSAGE_LIMIT,
            timezone=timezone,
        )
        self.sessions = sessions
        # A plain window, not this one: the stretch starts where the subsession opened, so a
        # clear made since leaves none of it out.
        self.subsession = ChatWindow(
            TelegramNotes(sessions, within=INSIDE_OPEN_SUBSESSION),
            self.vocabulary,
            count_tokens=count_tokens,
            token_budget=token_budget,
            edge=edge,
            edge_context_limit=EDGE_CONTEXT_MESSAGE_LIMIT,
            timezone=timezone,
        )

    async def recent(self, chat_id: int, **options: Any):
        if options.get("stop_at_edge", True):
            async with self.sessions() as session:
                options["after_message_id"] = await cleared_through(session, chat_id)
        return await super().recent(chat_id, **options)
