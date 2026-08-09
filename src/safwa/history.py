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
SUBSESSION_RESULT_HEADER = "📦 Subsession request"
_SUBSESSION_RESULT_START_RE = re.compile(
    r"^📦\s*Subsession request\s*\n(?P<body>[\s\S]*\S)\s*$", re.IGNORECASE
)
_SUBSESSION_RESULT_CONTINUED_RE = re.compile(
    r"^📦\s*Subsession request\s*\(continued\)\s*\n(?P<body>[\s\S]*\S)\s*$",
    re.IGNORECASE,
)
SUMMARY_CONTEXT_MESSAGE_LIMIT = 20


class HistoryBoundaryMissing(RuntimeError):
    """Raised when dialogue has no visible /newsession or Summary cut place."""


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
        require_boundary: bool = False,
    ) -> list[HistoryEntry]:
        if self.client is None:
            if require_boundary:
                raise HistoryBoundaryMissing(
                    "No Telegram history boundary is available. Start with /newsession followed "
                    "by your initial request."
                )
            return [source_message] if source_message else []
        async with self.sessions() as session:
            registry = list(
                await session.scalars(
                    select(TelegramMessage).where(TelegramMessage.chat_id == chat_id)
                )
            )
        used_registry_ids: set[int] = set()
        source_registration_seen = False
        # In a private Bot API chat, ``chat_id`` is the owner's user ID.  A
        # Telethon user session must read its dialog with the bot peer instead;
        # resolving ``chat_id`` would read the owner's Saved Messages.
        entity = await self.client.get_entity(self.bot_user_id)
        selected: list[HistoryEntry] = []
        summary_context: list[HistoryEntry] = []
        boundary: HistoryEntry | None = None
        subsession_result_chunks: list[str] = []
        scan_limit = max(1_000, limit * 20)
        async for message in self.client.iter_messages(entity, limit=scan_limit):
            raw_text = (getattr(message, "raw_text", None) or message.message or "").strip()
            if not raw_text:
                continue
            sender_id = int(message.sender_id) if message.sender_id else None
            created_at = message.date.astimezone(UTC)
            direction = "out" if sender_id == self.bot_user_id else "in"
            registration = self._registered_message(
                registry,
                used_registry_ids,
                message_id=message.id,
                direction=direction,
                created_at=created_at,
            )
            kind = registration.kind if registration is not None else None
            if (
                source_message is not None
                and registration is not None
                and direction == "in"
                and registration.message_id == source_message.message_id
            ):
                source_registration_seen = True

            if sender_id == self.bot_user_id:
                subsession_result = self._subsession_result_piece(raw_text)
                if subsession_result is not None:
                    is_start, body = subsession_result
                    subsession_result_chunks.append(body)
                    if is_start:
                        selected.append(
                            HistoryEntry(
                                message_id=message.id,
                                sender_id=sender_id,
                                role="user",
                                text="\n".join(reversed(subsession_result_chunks)),
                                created_at=created_at,
                                kind=MessageKind.SUBSESSION_RESULT.value,
                            )
                        )
                        subsession_result_chunks.clear()
                    continue
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
        has_subsession_result = any(
            entry.kind == MessageKind.SUBSESSION_RESULT.value for entry in selected
        )
        if require_boundary and boundary is None and not has_subsession_result:
            raise HistoryBoundaryMissing(
                "No /newsession or Summary boundary was found in Telegram. Start with "
                "/newsession followed by your initial request."
            )
        result = ([boundary] + summary_context + selected) if boundary else selected
        if (
            source_message
            and not source_registration_seen
            and all(item.message_id != source_message.message_id for item in result)
        ):
            result.append(source_message)
        return result

    @staticmethod
    def _registered_message(
        registry: list[TelegramMessage],
        used_registry_ids: set[int],
        *,
        message_id: int,
        direction: str,
        created_at: datetime,
    ) -> TelegramMessage | None:
        """Correlate Bot API registrations with Telethon's private-chat ID space."""
        exact = next(
            (
                row
                for row in registry
                if row.id not in used_registry_ids
                and row.direction == direction
                and row.message_id == message_id
            ),
            None,
        )
        if exact is not None:
            used_registry_ids.add(exact.id)
            return exact

        candidates: list[tuple[float, int, TelegramMessage]] = []
        for row in registry:
            if row.id in used_registry_ids or row.direction != direction:
                continue
            registered_at = row.created_at
            if registered_at.tzinfo is None:
                registered_at = registered_at.replace(tzinfo=UTC)
            difference = abs((registered_at.astimezone(UTC) - created_at).total_seconds())
            if difference <= 15:
                # Scanning is newest-first, so prefer the larger Bot API ID when
                # two registrations have the same timestamp distance.
                candidates.append((difference, -row.message_id, row))
        if not candidates:
            return None
        matched = min(candidates, key=lambda item: (item[0], item[1]))[2]
        used_registry_ids.add(matched.id)
        return matched

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
    def _subsession_result_piece(text: str) -> tuple[bool, str] | None:
        start = _SUBSESSION_RESULT_START_RE.match(text)
        if start is not None:
            return True, start.group("body").strip()
        continued = _SUBSESSION_RESULT_CONTINUED_RE.match(text)
        if continued is not None:
            return False, continued.group("body").strip()
        return None

    async def active_session_start(self, chat_id: int) -> HistoryEntry | None:
        """Return the newest real `/newsession` boundary, if it is still visible."""
        if self.client is None:
            return None
        entity = await self.client.get_entity(self.bot_user_id)
        async for message in self.client.iter_messages(entity, limit=1_000):
            raw_text = (getattr(message, "raw_text", None) or message.message or "").strip()
            if not raw_text or int(message.sender_id or 0) != self.owner_id:
                continue
            initial_request = self._new_session_request(raw_text)
            if initial_request is not None:
                return HistoryEntry(
                    message_id=message.id,
                    sender_id=self.owner_id,
                    role="user",
                    text=initial_request,
                    created_at=message.date.astimezone(UTC),
                    kind=MessageKind.SESSION_START.value,
                )
        return None

    @staticmethod
    def _dialogue_content(entry: HistoryEntry) -> str:
        if entry.kind == MessageKind.SUMMARY.value:
            return f"[Summary]: {entry.text}"
        if entry.kind == MessageKind.SESSION_START.value:
            return f"[Initial request]: {entry.text}"
        if entry.kind == MessageKind.SUBSESSION_RESULT.value:
            return f"[Subsession result]: {entry.text}"
        if entry.summary_context:
            stamp = entry.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
            return f"[{stamp}] {entry.role.title()}: {entry.text}"
        return entry.text

    async def dialogue(
        self, chat_id: int, *, source_message: HistoryEntry | None = None
    ) -> list[DialogueMessage]:
        entries = await self.recent(chat_id, source_message=source_message, require_boundary=True)
        dialogue: list[DialogueMessage] = []
        pending_user: list[str] = []

        def flush_user() -> None:
            if pending_user:
                dialogue.append(DialogueMessage(role="user", content="\n".join(pending_user)))
                pending_user.clear()

        for entry in entries:
            content = self._dialogue_content(entry)
            if entry.role == "assistant" and not entry.summary_context:
                flush_user()
                if dialogue and dialogue[-1].role == "assistant":
                    dialogue[-1] = replace(
                        dialogue[-1], content=dialogue[-1].content + "\n" + content
                    )
                else:
                    dialogue.append(DialogueMessage(role="assistant", content=content))
                continue
            # Summaries, the initial request, and the 20 timestamped messages
            # beside a Summary already have their own bracketed labels.
            if entry.summary_context or entry.kind in {
                MessageKind.SUMMARY.value,
                MessageKind.SESSION_START.value,
                MessageKind.SUBSESSION_RESULT.value,
            }:
                pending_user.append(content)
            else:
                pending_user.append(f"[User]: {content}")
        flush_user()
        return dialogue


async def register_message(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    direction: str,
    kind: MessageKind,
    related_id: int | None = None,
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
