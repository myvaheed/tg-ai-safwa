from __future__ import annotations

import asyncio
import getpass
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon import TelegramClient
from telethon.helpers import add_surrogate, del_surrogate

from .ai.context import DialogueMessage
from .config import Settings
from .constants import (
    HISTORY_SCAN_LIMIT,
    MESSAGE_CORRELATION_SECONDS,
    SUMMARY_CONTEXT_MESSAGE_LIMIT,
    SUMMARY_TRIGGER_TOKENS,
)
from .enums import MessageKind
from .memory import estimate_tokens
from .models import TelegramMessage

_SUMMARY_RE = re.compile(r"^📜\s*Summary\s*\n(?P<body>[\s\S]*\S)\s*$", re.IGNORECASE)


# Telegram carries the message text; `telegram_messages` only ever carried the label that
# says which of the bot's messages are persona dialogue.  That label cannot be derived at
# read time — a receipt and an advisor reply are both plain bot text — so it is written
# into the message itself as invisible characters and Telegram stays the whole record.
# Codes are append-only: a released code must never be reused for another kind.
_KIND_MARK_SENTINEL = "⁠"
_KIND_MARK_DIGITS = ("​", "‌")
_KIND_MARK_WIDTH = 5
_EVENT_MARK_WIDTH = 128
_KIND_MARK_CODES: dict[str, int] = {
    # 1 and 2 belonged to the retired session kinds and stay out of circulation.
    MessageKind.DIALOGUE_USER.value: 3,
    MessageKind.DIALOGUE_ASSISTANT.value: 4,
    MessageKind.REMINDER.value: 5,
    MessageKind.SUMMARY.value: 6,
    MessageKind.COMMAND.value: 7,
    MessageKind.UI_INPUT.value: 8,
    MessageKind.DASHBOARD.value: 9,
    MessageKind.CARD_EDITOR.value: 10,
    MessageKind.APPROVAL.value: 11,
    MessageKind.RECEIPT.value: 12,
    MessageKind.RETROSPECTIVE_PNG.value: 13,
    MessageKind.ERROR.value: 14,
}
_KIND_MARK_BY_CODE = {code: value for value, code in _KIND_MARK_CODES.items()}
_KIND_MARK_RE = re.compile(
    f"{_KIND_MARK_SENTINEL}"
    f"(?P<kind>[{''.join(_KIND_MARK_DIGITS)}]{{{_KIND_MARK_WIDTH}}})"
    f"(?P<event>[{''.join(_KIND_MARK_DIGITS)}]{{{_EVENT_MARK_WIDTH}}})$"
)


def _mark_digits(value: int, width: int) -> str:
    return "".join(
        _KIND_MARK_DIGITS[(value >> shift) & 1] for shift in reversed(range(width))
    )


def mark_message(
    text: str, kind: MessageKind, *, event_id: str | None = None
) -> tuple[str, str]:
    """Append an immutable invisible kind + event UUID and return both text and UUID."""
    event_id = event_id or uuid4().hex
    code = _KIND_MARK_CODES[kind.value]
    marker = (
        _KIND_MARK_SENTINEL
        + _mark_digits(code, _KIND_MARK_WIDTH)
        + _mark_digits(int(event_id, 16), _EVENT_MARK_WIDTH)
    )
    return f"{text}{marker}", event_id


def mark_kind(text: str, kind: MessageKind, *, event_id: str | None = None) -> str:
    """Append the invisible message marker that Telegram will keep for us."""
    return mark_message(text, kind, event_id=event_id)[0]


def read_message_mark(text: str) -> tuple[str | None, str | None, str]:
    """Split a marked message into kind, event UUID, and visible text."""
    match = _KIND_MARK_RE.search(text)
    if match is None:
        return None, None, text
    code = 0
    for digit in match.group("kind"):
        code = code * 2 + _KIND_MARK_DIGITS.index(digit)
    event_value = 0
    for digit in match.group("event"):
        event_value = event_value * 2 + _KIND_MARK_DIGITS.index(digit)
    return _KIND_MARK_BY_CODE.get(code), f"{event_value:032x}", text[: match.start()]


def read_kind_mark(text: str) -> tuple[str | None, str]:
    """Split a marked message into its kind and its visible text."""
    kind, _event_id, visible = read_message_mark(text)
    return kind, visible


# The same reasoning as the kind mark: an item citation is written as Markdown, sent as a
# link, and must read back as the Markdown the model wrote.  Telethon hands us plain text,
# so a link would otherwise return as bare words and teach the model that citing is optional.
CITATION_TYPES = ("card", "check", "tag", "value", "request")
CITATION_PATTERN = re.compile(
    r"\[([^\[\]\n]{1,120})\]\((" + "|".join(CITATION_TYPES) + r"):(\d{1,9})\)"
)
# A deep-link start payload accepts only [A-Za-z0-9_-], so the type separator differs.
_CITATION_PAYLOAD_RE = re.compile(r"^(" + "|".join(CITATION_TYPES) + r")-(\d{1,9})$")
_CITATION_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me"})


def citation_payload(item_type: str, item_id: int) -> str:
    return f"{item_type}-{item_id}"


def parse_citation_payload(payload: str) -> tuple[str, int] | None:
    match = _CITATION_PAYLOAD_RE.fullmatch(payload.strip())
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def restore_citations(text: str, entities: Sequence[Any] | None) -> str:
    """Rewrite the item links of a bot message back into `[text](card:12)` citations."""
    if not text or not entities:
        return text
    # Entity offsets count UTF-16 units, so every slice happens in surrogate space.
    surrogate = add_surrogate(text)
    found: list[tuple[int, int, str]] = []
    for entity in entities:
        target = _citation_from_url(getattr(entity, "url", None))
        if target is None:
            continue
        offset, length = int(entity.offset), int(entity.length)
        label = del_surrogate(surrogate[offset : offset + length])
        found.append((offset, length, f"[{label}]({target})"))
    for offset, length, citation in sorted(found, reverse=True):
        surrogate = surrogate[:offset] + add_surrogate(citation) + surrogate[offset + length :]
    return del_surrogate(surrogate)


def _citation_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.netloc.casefold() not in _CITATION_HOSTS:
        return None
    target = parse_citation_payload(parse_qs(parsed.query).get("start", [""])[0])
    return None if target is None else f"{target[0]}:{target[1]}"


def _aware(moment: datetime) -> datetime:
    """SQLite hands back naive datetimes; every comparison here is in UTC."""
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


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
        timezone: str = "UTC",
    ) -> None:
        self.client = client
        self.sessions = sessions
        self.bot_user_id = bot_user_id
        self.owner_id = owner_id
        self.tz = ZoneInfo(timezone)

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
            timezone=settings.timezone,
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
        token_budget: int = SUMMARY_TRIGGER_TOKENS,
        since: datetime | None = None,
        until: datetime | None = None,
        source_message: HistoryEntry | None = None,
        stop_at_summary: bool = True,
    ) -> list[HistoryEntry]:
        """The dialogue window: the newest Summary plus as many messages as fit.

        The scan walks backwards and stops at the first of three edges — ``since``, the
        newest Summary, or ``token_budget`` spent.  The budget is checked before an entry
        is taken, so the cut always lands between messages.  ``until`` skips everything
        newer instead of stopping, which is what closes a past day at both ends.

        ``stop_at_summary=False`` skips Summaries instead of treating one as an edge, for
        a caller that asked for a period rather than for a window: a Summary written at
        noon must not cut that day in half.
        """
        if self.client is None:
            return [source_message] if source_message else []
        async with self.sessions() as session:
            registry = list(
                await session.scalars(
                    select(TelegramMessage).where(TelegramMessage.chat_id == chat_id)
                )
            )
        since = _aware(since) if since else None
        until = _aware(until) if until else None
        registry_by_event = {row.event_id: row for row in registry if row.event_id}
        used_registry_ids: set[int] = set()
        source_registration_seen = False
        # Owner text carries no kind mark, so the oldest registration is the only floor on
        # how far back this chat is Safwa's at all.  A rebuilt database has none, and the
        # kind marks on the bot's own messages carry the classification instead.
        floor = (
            min(_aware(row.created_at) for row in registry)
            - timedelta(seconds=MESSAGE_CORRELATION_SECONDS)
            if registry
            else None
        )
        # In a private Bot API chat, ``chat_id`` is the owner's user ID.  A
        # Telethon user session must read its dialog with the bot peer instead;
        # resolving ``chat_id`` would read the owner's Saved Messages.
        entity = await self.client.get_entity(self.bot_user_id)
        selected: list[HistoryEntry] = []
        summary_context: list[HistoryEntry] = []
        boundary: HistoryEntry | None = None
        spent = 0
        async for message in self.client.iter_messages(entity, limit=HISTORY_SCAN_LIMIT):
            marked_kind, marked_event_id, raw_text = read_message_mark(
                restore_citations(
                    getattr(message, "raw_text", None) or message.message or "",
                    getattr(message, "entities", None),
                ).strip()
            )
            raw_text = raw_text.strip()
            created_at = message.date.astimezone(UTC)
            if (floor is not None and created_at < floor) or (
                since is not None and created_at <= since
            ):
                break
            if until is not None and created_at >= until:
                continue
            if not raw_text:
                continue
            sender_id = int(message.sender_id) if message.sender_id else None
            direction = "out" if sender_id == self.bot_user_id else "in"
            registration = (
                registry_by_event.get(marked_event_id)
                if direction == "out" and marked_event_id is not None
                else self._registered_message(
                    registry,
                    used_registry_ids,
                    message_id=message.id,
                    direction=direction,
                    created_at=created_at,
                )
                if direction == "in"
                else None
            )
            kind = marked_kind or (registration.kind if registration is not None else None)
            if (
                source_message is not None
                and registration is not None
                and registration.message_id == source_message.message_id
            ):
                source_registration_seen = True

            if sender_id == self.bot_user_id:
                summary = self._summary_body(raw_text)
                if summary is not None:
                    if not stop_at_summary:
                        continue
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
                if kind == MessageKind.DIALOGUE_USER.value:
                    role = "user"
                elif kind in {
                    MessageKind.DIALOGUE_ASSISTANT.value,
                    MessageKind.REMINDER.value,
                }:
                    role = "assistant"
                else:
                    continue
            elif sender_id == self.owner_id:
                if kind is None and not raw_text.startswith("/"):
                    # The owner's client cannot carry a kind mark, so Telegram itself is
                    # the evidence: commands and typed field input are deleted from the
                    # chat, so surviving owner text is dialogue.
                    kind = MessageKind.DIALOGUE_USER.value
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
            cost = estimate_tokens(entry.text)
            if (selected or summary_context) and spent + cost > token_budget:
                break
            spent += cost
            if boundary is not None:
                if len(summary_context) >= SUMMARY_CONTEXT_MESSAGE_LIMIT:
                    break
                summary_context.append(replace(entry, summary_context=True))
            else:
                selected.append(entry)

        selected.reverse()
        summary_context.reverse()
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
        """Correlate Bot API registrations with Telethon's private-chat ID space.

        Timestamps decide first.  The two ID spaces are independent but can overlap, so a
        bare ID match is as likely to be a collision with an unrelated older registration
        as it is to be the real row — and consuming the wrong row silently drops a
        message from the LLM's view of the dialogue.  An exact ID only breaks a tie
        inside the correlation window, or stands alone when no registration is close
        enough in time to be a candidate at all.
        """
        candidates: list[tuple[int, float, int, TelegramMessage]] = []
        for row in registry:
            if row.id in used_registry_ids or row.direction != direction:
                continue
            difference = abs((_aware(row.created_at) - created_at).total_seconds())
            if difference <= MESSAGE_CORRELATION_SECONDS:
                # Scanning is newest-first, so prefer the larger Bot API ID when
                # two registrations have the same timestamp distance.
                candidates.append(
                    (0 if row.message_id == message_id else 1, difference, -row.message_id, row)
                )
        if not candidates:
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
        matched = min(candidates, key=lambda item: item[:3])[3]
        used_registry_ids.add(matched.id)
        return matched

    @staticmethod
    def _summary_body(text: str) -> str | None:
        match = _SUMMARY_RE.match(text)
        return match.group("body").strip() if match else None

    @staticmethod
    def _dialogue_content(entry: HistoryEntry, stamp: str | None) -> str:
        head = f"[{stamp}] " if stamp else ""
        if entry.kind == MessageKind.SUMMARY.value:
            return f"{head}[Summary]: {entry.text}"
        # An assistant turn is already an assistant-role message; only owner text and the
        # messages packed beside a Summary need to say whose they are.
        if entry.summary_context or entry.role == "user":
            return f"{head}[{entry.role.title()}]: {entry.text}"
        return f"{head}{entry.text}"

    async def day_transcript(
        self, chat_id: int, *, start: datetime, end: datetime, token_budget: int
    ) -> str:
        """One period of dialogue as plain text, oldest first, stamped to the minute.

        Read by a model that does not have the conversation, so every line says who spoke.
        """
        entries = await self.recent(
            chat_id,
            token_budget=token_budget,
            since=start,
            until=end,
            stop_at_summary=False,
        )
        return "\n".join(
            f"[{entry.created_at.astimezone(self.tz):%H:%M}] "
            f"[{entry.role.title()}]: {entry.text}"
            for entry in entries
        )

    async def dialogue(
        self, chat_id: int, *, source_message: HistoryEntry | None = None
    ) -> list[DialogueMessage]:
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
            content = self._dialogue_content(entry, stamp)
            if entry.role == "assistant" and not entry.summary_context:
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
