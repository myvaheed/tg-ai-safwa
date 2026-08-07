from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from safwa.enums import MessageKind
from safwa.history import HistoryEntry, TelegramHistorySource, register_message


@dataclass
class FakeTelegramMessage:
    id: int
    raw_text: str
    sender_id: int
    date: datetime


class FakeTelegramClient:
    def __init__(self, messages: list[FakeTelegramMessage]) -> None:
        self.messages = messages

    async def get_entity(self, chat_id: int) -> int:
        return chat_id

    async def iter_messages(self, _entity: int, *, limit: int):
        for message in self.messages[:limit]:
            yield message


async def register(
    sessions, chat_id: int, message_id: int, direction: str, kind: MessageKind
) -> None:
    async with sessions() as session:
        await register_message(session, chat_id, message_id, direction, kind)
        await session.commit()


async def test_history_fails_closed_and_starts_at_newsession(sessions) -> None:
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(9, "Hello Safwa", owner_id, at + timedelta(minutes=4)),
        FakeTelegramMessage(
            8, "A card title entered in a form", owner_id, at + timedelta(minutes=3)
        ),
        FakeTelegramMessage(7, "Hello — how can I help?", bot_id, at + timedelta(minutes=2)),
        FakeTelegramMessage(6, "<b>Today</b>", bot_id, at + timedelta(minutes=1)),
        FakeTelegramMessage(5, "/newsession Help me plan a calmer week", owner_id, at),
        FakeTelegramMessage(
            4, "Old unrelated private-chat message", owner_id, at - timedelta(minutes=1)
        ),
        FakeTelegramMessage(3, "An old message from the bot", bot_id, at - timedelta(minutes=2)),
    ]
    await register(sessions, chat_id, 9, "in", MessageKind.DIALOGUE_USER)
    await register(sessions, chat_id, 8, "in", MessageKind.UI_INPUT)
    await register(sessions, chat_id, 7, "out", MessageKind.DIALOGUE_ASSISTANT)
    await register(sessions, chat_id, 6, "out", MessageKind.DASHBOARD)
    await register(sessions, chat_id, 5, "in", MessageKind.SESSION_START)
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.SESSION_START.value, "Help me plan a calmer week"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Hello — how can I help?"),
        (MessageKind.DIALOGUE_USER.value, "Hello Safwa"),
    ]
    dialogue = await source.dialogue(chat_id)
    assert dialogue[0].content == "[Initial request]: Help me plan a calmer week"
    assert [message.role for message in dialogue] == ["user", "assistant", "user"]


async def test_summary_is_pinned_first_with_twenty_timestamped_prior_messages(sessions) -> None:
    chat_id, owner_id, bot_id = 101, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    older = [
        FakeTelegramMessage(
            message_id,
            f"Older Safwa dialogue {message_id}",
            owner_id,
            at + timedelta(minutes=message_id),
        )
        for message_id in range(97, 67, -1)
    ]
    messages = [
        FakeTelegramMessage(100, "Current turn", owner_id, at + timedelta(minutes=100)),
        FakeTelegramMessage(99, "Recent Safwa answer", bot_id, at + timedelta(minutes=99)),
        FakeTelegramMessage(
            98, "📜 Summary\nThe important earlier context.", bot_id, at + timedelta(minutes=98)
        ),
        *older,
        FakeTelegramMessage(
            67, "/newsession Start this project", owner_id, at + timedelta(minutes=67)
        ),
    ]
    await register(sessions, chat_id, 100, "in", MessageKind.DIALOGUE_USER)
    await register(sessions, chat_id, 99, "out", MessageKind.DIALOGUE_ASSISTANT)
    await register(sessions, chat_id, 98, "out", MessageKind.SUMMARY)
    for message in older:
        await register(sessions, chat_id, message.id, "in", MessageKind.DIALOGUE_USER)
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id)

    assert entries[0].kind == MessageKind.SUMMARY.value
    assert entries[0].text == "The important earlier context."
    assert [entry.message_id for entry in entries[1:21]] == list(range(78, 98))
    assert all(entry.summary_context for entry in entries[1:21])
    assert [entry.message_id for entry in entries[21:]] == [99, 100]

    dialogue = await source.dialogue(chat_id)
    assert dialogue[0].content == "[Summary]: The important earlier context."
    assert dialogue[1].role == "user"
    assert dialogue[1].content == "[2026-08-08 13:18 UTC] User: Older Safwa dialogue 78"
    assert dialogue[-2].role == "assistant"
    assert dialogue[-1].content == "Current turn"


async def test_current_source_is_kept_but_unknown_historical_telegram_text_is_excluded(
    sessions,
) -> None:
    chat_id, owner_id, bot_id = 102, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    source_message = HistoryEntry(
        message_id=3,
        sender_id=owner_id,
        role="user",
        text="Привет",
        created_at=at,
        kind=MessageKind.DIALOGUE_USER.value,
    )
    source = TelegramHistorySource(
        FakeTelegramClient(
            [
                FakeTelegramMessage(3, "Привет", owner_id, at),
                FakeTelegramMessage(2, "Old unrelated text", owner_id, at - timedelta(minutes=1)),
                FakeTelegramMessage(1, "Old bot UI", bot_id, at - timedelta(minutes=2)),
            ]
        ),
        sessions,
        bot_user_id=bot_id,
        owner_id=owner_id,
    )
    # Even a semantically registered older message is excluded without an
    # explicit session or Summary boundary.
    await register(sessions, chat_id, 2, "in", MessageKind.DIALOGUE_USER)

    entries = await source.recent(chat_id, source_message=source_message)

    assert entries == [source_message]
