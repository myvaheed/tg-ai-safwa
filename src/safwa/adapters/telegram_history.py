"""Safwa's chat: the Telethon reader, the note table, and what its kinds mean.

The window itself is `telegram_llm`. This is what Safwa has to tell it — which of its
message kinds are the conversation, what a Summary is headed, what it cites — plus the two
things only Safwa can supply: the real private chat through a Telethon user session, and
the `telegram_messages` table the notes live in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection, Sequence
from datetime import datetime

from sqlalchemy import (
    Integer,
    String,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column
from telethon import TelegramClient

from telegram_llm import (
    ChatMessage,
    ChatVocabulary,
    ChatWindow,
    KindMarks,
    Note,
)

from ..constants import SUMMARY_TRIGGER_TOKENS
from ..features.continuity.model import SUMMARY_HEADER
from ..features.proposals.model import RECEIPT_MEANINGS
from ..foundation.models import Base, UtcDateTime
from ..foundation.tokens import estimate_tokens
from .kinds import MessageKind

__all__ = [
    "TelegramHistorySource",
    "TelegramMessage",
    "TelegramNotes",
    "register_message",
]

SUMMARY_CONTEXT_MESSAGE_LIMIT = 20


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(40), default=MessageKind.DASHBOARD.value)
    related_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("chat_id", "message_id"),)


def vocabulary(citation_types: tuple[str, ...]) -> ChatVocabulary:
    """How the reader reads the chat back. The citation types are the features' own."""
    return ChatVocabulary(
        person=MessageKind.DIALOGUE_USER.value,
        assistant=frozenset({MessageKind.DIALOGUE_ASSISTANT.value, MessageKind.CUE.value}),
        summary=MessageKind.SUMMARY.value,
        summary_header=SUMMARY_HEADER,
        citation_types=citation_types,
        receipts=RECEIPT_MEANINGS,
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
        return [
            Note(
                chat_id=row.chat_id,
                message_id=row.message_id,
                direction=row.direction,
                kind=row.kind,
                related_id=row.related_id,
                event_id=row.event_id,
            )
            for row in rows
        ]

    async def note(self, chat_id: int, message_id: int) -> Note | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(TelegramMessage).where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.message_id == message_id,
                )
            )
        if row is None:
            return None
        return Note(
            chat_id=row.chat_id,
            message_id=row.message_id,
            direction=row.direction,
            kind=row.kind,
            related_id=row.related_id,
            event_id=row.event_id,
        )

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
        if event_id is not None:
            existing.event_id = event_id
    else:
        session.add(
            TelegramMessage(
                chat_id=chat_id,
                message_id=message_id,
                event_id=event_id,
                direction=direction,
                kind=kind.value,
                related_id=related_id,
            )
        )


class TelegramHistorySource(ChatWindow):
    """The window over the real private chat, read through a Telethon user session.

    It is its own reader: the chat it reads and the window it produces are one thing here,
    because only a Telethon session can read a chat a bot is in.
    """

    def __init__(
        self,
        client: TelegramClient | None,
        sessions: async_sessionmaker[AsyncSession],
        *,
        marks: KindMarks,
        bot_user_id: int,
        owner_id: int,
        citation_types: tuple[str, ...] = (),
        timezone: str = "UTC",
    ) -> None:
        super().__init__(
            self if client is not None else None,
            TelegramNotes(sessions),
            marks,
            vocabulary(citation_types),
            bot_user_id=bot_user_id,
            owner_id=owner_id,
            count_tokens=estimate_tokens,
            token_budget=SUMMARY_TRIGGER_TOKENS,
            summary_context_limit=SUMMARY_CONTEXT_MESSAGE_LIMIT,
            timezone=timezone,
        )
        self.client = client
        self.sessions = sessions

    async def messages(self, limit: int) -> AsyncIterator[ChatMessage]:
        # In a private Bot API chat, the chat id is the owner's user ID.  A Telethon user
        # session must read its dialog with the bot peer instead; resolving the chat id
        # would read the owner's Saved Messages.
        entity = await self.client.get_entity(self.bot_user_id)
        async for message in self.client.iter_messages(entity, limit=limit):
            yield ChatMessage(
                id=message.id,
                text=getattr(message, "raw_text", None) or message.message or "",
                sender_id=int(message.sender_id) if message.sender_id else None,
                date=message.date,
                entities=getattr(message, "entities", None),
            )

    async def start(self) -> None:
        if self.client is None:
            return
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise RuntimeError(
                "Telegram history session is not authorized; run `uv run safwa-auth`"
            )

    async def close(self) -> None:
        if self.client:
            await self.client.disconnect()
