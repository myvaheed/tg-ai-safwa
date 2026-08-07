from __future__ import annotations

import asyncio
import getpass
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon import TelegramClient

from .ai.context import DialogueMessage
from .config import Settings
from .enums import MessageKind
from .models import SummaryState, TelegramMessage

DIALOGUE_KINDS = {
    MessageKind.DIALOGUE_USER.value,
    MessageKind.DIALOGUE_ASSISTANT.value,
    MessageKind.REMINDER.value,
    MessageKind.SUMMARY.value,
}


@dataclass(frozen=True)
class HistoryEntry:
    message_id: int
    sender_id: int | None
    role: str
    text: str
    created_at: datetime
    kind: str


class TelegramHistorySource:
    """Read canonical persona dialogue from an authorized Telegram user session."""

    def __init__(
        self,
        client: TelegramClient | None,
        sessions: async_sessionmaker[AsyncSession],
        *,
        bot_user_id: int,
        owner_id: int,
    ) -> None:
        self.client = client
        self.sessions = sessions
        self.bot_user_id = bot_user_id
        self.owner_id = owner_id

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        *,
        bot_user_id: int,
    ) -> TelegramHistorySource:
        client = None
        if settings.telegram_history_enabled:
            client = TelegramClient(
                str(settings.telegram_user_session_path),
                settings.telegram_api_id,
                settings.telegram_api_hash.get_secret_value(),  # type: ignore[union-attr]
            )
        return cls(
            client,
            sessions,
            bot_user_id=bot_user_id,
            owner_id=settings.telegram_owner_id,
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

    async def recent(
        self,
        chat_id: int,
        *,
        limit: int = 120,
        source_message: HistoryEntry | None = None,
    ) -> list[HistoryEntry]:
        if self.client is None:
            return [source_message] if source_message else []
        async with self.sessions() as session:
            registry = {
                row.message_id: row.kind
                for row in await session.scalars(
                    select(TelegramMessage).where(TelegramMessage.chat_id == chat_id)
                )
            }
            summary = await session.get(SummaryState, 1)
            boundary = summary.summary_message_id if summary else None
        entity = await self.client.get_entity(chat_id)
        result: list[HistoryEntry] = []
        async for message in self.client.iter_messages(entity, limit=limit):
            if not message.message:
                continue
            sender_id = int(message.sender_id) if message.sender_id else None
            kind = registry.get(message.id)
            if sender_id == self.owner_id:
                if kind and kind not in DIALOGUE_KINDS:
                    continue
                if not kind and message.message.lstrip().startswith("/"):
                    continue
                kind = kind or MessageKind.DIALOGUE_USER.value
                role = "user"
            elif sender_id == self.bot_user_id:
                if kind not in DIALOGUE_KINDS:
                    continue
                role = "assistant"
            else:
                continue
            result.append(
                HistoryEntry(
                    message_id=message.id,
                    sender_id=sender_id,
                    role=role,
                    text=message.message,
                    created_at=message.date.astimezone(UTC),
                    kind=kind,
                )
            )
            if boundary and message.id == boundary:
                break
        result.reverse()
        if source_message and all(item.message_id != source_message.message_id for item in result):
            result.append(source_message)
        return result

    async def dialogue(
        self, chat_id: int, *, source_message: HistoryEntry | None = None
    ) -> list[DialogueMessage]:
        entries = await self.recent(chat_id, source_message=source_message)
        return [DialogueMessage(role=item.role, content=item.text) for item in entries]


async def register_message(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    direction: str,
    kind: MessageKind,
    related_id: str | None = None,
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
    else:
        session.add(
            TelegramMessage(
                chat_id=chat_id,
                message_id=message_id,
                direction=direction,
                kind=kind.value,
                related_id=related_id,
            )
        )


def auth_main() -> None:
    settings = Settings()
    if not settings.telegram_history_enabled:
        raise SystemExit("Set SAFWA_TELEGRAM_API_ID and SAFWA_TELEGRAM_API_HASH first")

    async def authenticate() -> None:
        Path(settings.telegram_user_session_path).parent.mkdir(parents=True, exist_ok=True)
        client = TelegramClient(
            str(settings.telegram_user_session_path),
            settings.telegram_api_id,
            settings.telegram_api_hash.get_secret_value(),  # type: ignore[union-attr]
        )
        await client.start(
            phone=lambda: input("Telegram phone: "),
            code_callback=lambda: input("Telegram code: "),
            password=lambda: getpass.getpass("2FA password: "),
        )
        await client.disconnect()

    asyncio.run(authenticate())


if __name__ == "__main__":
    auth_main()
