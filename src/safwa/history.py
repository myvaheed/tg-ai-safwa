from __future__ import annotations

import asyncio
import getpass
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon import TelegramClient

from .ai.context import DialogueMessage
from .config import Settings
from .enums import MessageKind
from .models import TelegramMessage

_NEW_SESSION_RE = re.compile(
    r"^/newsession(?:@[A-Za-z0-9_]+)?(?:\s+(?P<body>.*\S))?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_SUMMARY_RE = re.compile(r"^📜\s*Summary\s*\n(?P<body>[\s\S]*\S)\s*$", re.IGNORECASE)
SUMMARY_CONTEXT_MESSAGE_LIMIT = 20


@dataclass(frozen=True)
class HistoryEntry:
    message_id: int
    sender_id: int | None
    role: str
    text: str
    created_at: datetime
    kind: str
    summary_context: bool = False


class TelegramHistorySource:
    """Read the canonical, filtered Safwa transcript from Telegram itself."""

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
        entity = await self.client.get_entity(chat_id)
        selected: list[HistoryEntry] = []
        summary_context: list[HistoryEntry] = []
        boundary: HistoryEntry | None = None
        scan_limit = max(1_000, limit * 20)
        async for message in self.client.iter_messages(entity, limit=scan_limit):
            raw_text = (getattr(message, "raw_text", None) or message.message or "").strip()
            if not raw_text:
                continue
            sender_id = int(message.sender_id) if message.sender_id else None
            kind = registry.get(message.id)
            created_at = message.date.astimezone(UTC)

            if sender_id == self.bot_user_id:
                summary = self._summary_body(raw_text)
                if summary is not None:
                    if boundary is None:
                        boundary = HistoryEntry(
                            message_id=message.id,
                            sender_id=sender_id,
                            role="user",
                            text=summary,
                            created_at=created_at,
                            kind=MessageKind.SUMMARY.value,
                        )
                    # An older summary is already represented by the nearest one.
                    continue
                if kind not in {
                    MessageKind.DIALOGUE_ASSISTANT.value,
                    MessageKind.REMINDER.value,
                }:
                    continue
                role = "assistant"
            elif sender_id == self.owner_id:
                initial_request = self._new_session_request(raw_text)
                if initial_request is not None:
                    if boundary is None:
                        boundary = HistoryEntry(
                            message_id=message.id,
                            sender_id=sender_id,
                            role="user",
                            text=initial_request,
                            created_at=created_at,
                            kind=MessageKind.SESSION_START.value,
                        )
                    # A new Safwa session is always the outer history boundary.
                    break
                # Unknown human Telegram traffic is never dialogue.  This prevents
                # pre-Safwa/private-chat history and UI/form input leaking to the LLM.
                if kind != MessageKind.DIALOGUE_USER.value:
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
                kind=kind or MessageKind.DIALOGUE_USER.value,
            )
            if boundary and boundary.kind == MessageKind.SUMMARY.value:
                if len(summary_context) >= SUMMARY_CONTEXT_MESSAGE_LIMIT:
                    break
                summary_context.append(replace(entry, summary_context=True))
                if len(summary_context) >= SUMMARY_CONTEXT_MESSAGE_LIMIT:
                    break
            elif len(selected) < limit:
                selected.append(entry)

        selected.reverse()
        summary_context.reverse()
        # A private bot chat may predate Safwa.  Until an explicit `/newsession`
        # or visible Summary establishes a real boundary, no prior message is safe
        # to treat as Safwa persona history.
        result = ([boundary] + summary_context + selected) if boundary else []
        if source_message and all(item.message_id != source_message.message_id for item in result):
            result.append(source_message)
        return result

    @staticmethod
    def _new_session_request(text: str) -> str | None:
        match = _NEW_SESSION_RE.match(text)
        if match is None or not match.group("body"):
            return None
        return match.group("body").strip()

    @staticmethod
    def _summary_body(text: str) -> str | None:
        match = _SUMMARY_RE.match(text)
        return match.group("body").strip() if match else None

    @staticmethod
    def _dialogue_content(entry: HistoryEntry) -> str:
        if entry.kind == MessageKind.SUMMARY.value:
            return f"[Summary]: {entry.text}"
        if entry.kind == MessageKind.SESSION_START.value:
            return f"[Initial request]: {entry.text}"
        if entry.summary_context:
            stamp = entry.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
            return f"[{stamp}] {entry.role.title()}: {entry.text}"
        return entry.text

    async def dialogue(
        self, chat_id: int, *, source_message: HistoryEntry | None = None
    ) -> list[DialogueMessage]:
        entries = await self.recent(chat_id, source_message=source_message)
        return [
            DialogueMessage(
                # Summary context is deliberately supplied after the Summary as
                # timestamped reference material, rather than as a new assistant turn.
                role="user" if entry.summary_context else entry.role,
                content=self._dialogue_content(entry),
            )
            for entry in entries
        ]


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
