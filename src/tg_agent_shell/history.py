"""The bot's chat: the table it is kept in, and what its kinds mean.

The window itself is `telegram_llm`. This is what it has to be told — which message kinds
are the conversation and what each cites — plus the one thing only a running bot can
supply: the `telegram_messages` table every message is kept in as it passes. Where the
window ends arrives the same way, as a `WindowEdge`.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from datetime import datetime

from sqlalchemy import (
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from telegram_llm import ChatVocabulary, ChatWindow, Note, WindowEdge

from .foundation.kinds import MessageKind
from .foundation.models import Base, UtcDateTime
from .proposals.model import RECEIPT_MEANINGS

__all__ = [
    "TelegramHistorySource",
    "TelegramMessage",
    "TelegramNotes",
    "register_message",
]

EDGE_CONTEXT_MESSAGE_LIMIT = 20


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(40), default=MessageKind.DASHBOARD.value)
    related_id: Mapped[int | None] = mapped_column(Integer)
    # The message as it stands in the chat, in Telegram HTML; None when its words are not kept.
    text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("chat_id", "message_id"),)


def vocabulary(citation_types: tuple[str, ...]) -> ChatVocabulary:
    """How the reader reads the chat back. The citation types are the features' own."""
    return ChatVocabulary(
        person=MessageKind.DIALOGUE_USER.value,
        assistant=frozenset({MessageKind.DIALOGUE_ASSISTANT.value, MessageKind.CUE.value}),
        citation_types=citation_types,
        receipts=RECEIPT_MEANINGS,
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
    )


class TelegramNotes:
    """The window's `NoteStore`, over `telegram_messages`."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

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
        async with self.sessions() as session:
            rows = list(
                await session.scalars(
                    select(TelegramMessage)
                    .where(
                        TelegramMessage.chat_id == chat_id,
                        TelegramMessage.text.is_not(None),
                    )
                    .order_by(TelegramMessage.message_id.desc())
                    .limit(limit)
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
) -> None:
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
        )
        if at is not None:
            row.created_at = at
        session.add(row)


class TelegramHistorySource(ChatWindow):
    """The window over the chat as the bot keeps it in `telegram_messages`."""

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
            TelegramNotes(sessions),
            vocabulary(citation_types),
            count_tokens=count_tokens,
            token_budget=token_budget,
            edge=edge,
            edge_context_limit=EDGE_CONTEXT_MESSAGE_LIMIT,
            timezone=timezone,
        )
        self.sessions = sessions
