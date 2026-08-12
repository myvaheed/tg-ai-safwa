from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from safwa.enums import MessageKind
from safwa.history import (
    HistoryBoundaryMissing,
    HistoryEntry,
    TelegramHistorySource,
    mark_kind,
    read_kind_mark,
    register_message,
)
from safwa.models import TelegramMessage


@dataclass
class FakeTelegramMessage:
    id: int
    raw_text: str
    sender_id: int
    date: datetime
    reply_markup: object | None = None


class FakeTelegramClient:
    def __init__(self, messages: list[FakeTelegramMessage]) -> None:
        self.messages = messages
        self.entity_ids: list[int] = []

    async def get_entity(self, chat_id: int) -> int:
        self.entity_ids.append(chat_id)
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
    assert (await source.active_session_start(chat_id)).message_id == 5
    assert source.client.entity_ids == [bot_id, bot_id, bot_id]


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
    assert dialogue[0].role == "user"
    assert dialogue[0].content.startswith("[Summary]: The important earlier context.")
    assert "[2026-08-08 13:18 UTC] User: Older Safwa dialogue 78" in dialogue[0].content
    assert dialogue[-2].role == "assistant"
    assert dialogue[-1].content == "[User]: Current turn"


async def test_private_chat_correlates_telethon_and_bot_api_message_ids(sessions) -> None:
    chat_id, owner_id, bot_id = 104, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(95_003, "Continue", owner_id, at + timedelta(seconds=2)),
        FakeTelegramMessage(95_002, "Safwa answer", bot_id, at + timedelta(seconds=1)),
        FakeTelegramMessage(95_001, "/newsession Initial request", owner_id, at),
    ]
    await register(sessions, chat_id, 13, "in", MessageKind.DIALOGUE_USER)
    await register(sessions, chat_id, 12, "out", MessageKind.DIALOGUE_ASSISTANT)
    async with sessions() as session:
        await session.execute(
            update(TelegramMessage)
            .where(TelegramMessage.chat_id == chat_id, TelegramMessage.message_id == 13)
            .values(created_at=at + timedelta(seconds=3))
        )
        await session.execute(
            update(TelegramMessage)
            .where(TelegramMessage.chat_id == chat_id, TelegramMessage.message_id == 12)
            .values(created_at=at + timedelta(seconds=2))
        )
        await session.commit()
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id, require_boundary=True)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.SESSION_START.value, "Initial request"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Safwa answer"),
        (MessageKind.DIALOGUE_USER.value, "Continue"),
    ]


async def test_current_source_is_not_duplicated_across_telegram_id_spaces(sessions) -> None:
    chat_id, owner_id, bot_id = 105, 42, 99
    at = datetime(2026, 8, 9, 12, 47, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(95_003, "Как дела?", owner_id, at + timedelta(seconds=2)),
        FakeTelegramMessage(95_002, "Safwa answer", bot_id, at + timedelta(seconds=1)),
        FakeTelegramMessage(95_001, "/newsession Initial request", owner_id, at),
    ]
    await register(sessions, chat_id, 13, "in", MessageKind.DIALOGUE_USER)
    await register(sessions, chat_id, 12, "out", MessageKind.DIALOGUE_ASSISTANT)
    async with sessions() as session:
        await session.execute(
            update(TelegramMessage)
            .where(TelegramMessage.chat_id == chat_id, TelegramMessage.message_id == 13)
            .values(created_at=at + timedelta(seconds=3))
        )
        await session.execute(
            update(TelegramMessage)
            .where(TelegramMessage.chat_id == chat_id, TelegramMessage.message_id == 12)
            .values(created_at=at + timedelta(seconds=2))
        )
        await session.commit()
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )
    current = HistoryEntry(
        message_id=13,
        sender_id=owner_id,
        role="user",
        text="Как дела?",
        created_at=at + timedelta(seconds=3),
        kind=MessageKind.DIALOGUE_USER.value,
    )

    dialogue = await source.dialogue(chat_id, source_message=current)

    assert dialogue[-1].role == "user"
    assert dialogue[-1].content == "[User]: Как дела?"


async def test_dialogue_requires_a_newsession_or_summary_boundary(
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
    await register(sessions, chat_id, 2, "in", MessageKind.DIALOGUE_USER)

    with pytest.raises(HistoryBoundaryMissing, match="/newsession or Summary"):
        await source.dialogue(chat_id, source_message=source_message)


async def test_subsession_result_is_reassembled_as_parent_context(sessions) -> None:
    chat_id, owner_id, bot_id = 103, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(5, "What should I do next?", owner_id, at + timedelta(minutes=3)),
        FakeTelegramMessage(
            4,
            "📦 Subsession request (continued)\nSecond result part.",
            bot_id,
            at + timedelta(minutes=2),
        ),
        FakeTelegramMessage(
            3, "📦 Subsession request\nFirst result part.", bot_id, at + timedelta(minutes=1)
        ),
        FakeTelegramMessage(2, "Unrelated old private-chat text", owner_id, at),
    ]
    await register(sessions, chat_id, 5, "in", MessageKind.DIALOGUE_USER)
    await register(sessions, chat_id, 4, "out", MessageKind.SUBSESSION_RESULT)
    await register(sessions, chat_id, 3, "out", MessageKind.SUBSESSION_RESULT)
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.SUBSESSION_RESULT.value, "First result part.\nSecond result part."),
        (MessageKind.DIALOGUE_USER.value, "What should I do next?"),
    ]
    dialogue = await source.dialogue(chat_id)
    assert dialogue == [
        type(dialogue[0])(
            role="user",
            content="[Subsession result]: First result part.\nSecond result part.\n"
            "[User]: What should I do next?",
        )
    ]


async def test_dialogue_groups_every_user_message_until_the_next_ai_response(sessions) -> None:
    source = TelegramHistorySource(None, sessions, bot_user_id=99, owner_id=42)
    entries = [
        HistoryEntry(1, 42, "user", "First thought", datetime.now(UTC), "dialogue_user"),
        HistoryEntry(2, 42, "user", "Second thought", datetime.now(UTC), "dialogue_user"),
        HistoryEntry(3, 99, "assistant", "Safwa reply", datetime.now(UTC), "dialogue_assistant"),
        HistoryEntry(4, 42, "user", "Follow-up", datetime.now(UTC), "dialogue_user"),
    ]

    async def recent(*_args, **_kwargs):
        return entries

    source.recent = recent  # type: ignore[method-assign]
    dialogue = await source.dialogue(100)

    assert [(item.role, item.content) for item in dialogue] == [
        ("user", "[User]: First thought\n[User]: Second thought"),
        ("assistant", "Safwa reply"),
        ("user", "[User]: Follow-up"),
    ]


async def test_colliding_id_space_does_not_drop_the_newest_dialogue_message(sessions) -> None:
    """A stale registration that happens to share a Telethon ID must not steal the match."""
    chat_id, owner_id, bot_id = 106, 42, 99
    at = datetime(2026, 8, 11, 21, 33, 33, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(406, "Создай задачу подтянуться 20 раз", owner_id, at),
        FakeTelegramMessage(405, "Цель удалена.", bot_id, at - timedelta(seconds=30)),
        FakeTelegramMessage(404, "/newsession Начнём", owner_id, at - timedelta(minutes=5)),
    ]
    await register(sessions, chat_id, 465, "in", MessageKind.DIALOGUE_USER)
    await register(sessions, chat_id, 464, "out", MessageKind.DIALOGUE_ASSISTANT)
    # An older form input whose Bot API ID collides with the newest Telethon ID.
    await register(sessions, chat_id, 406, "in", MessageKind.UI_INPUT)
    async with sessions() as session:
        for message_id, registered_at in (
            (465, at),
            (464, at - timedelta(seconds=30)),
            (406, at - timedelta(hours=2)),
        ):
            await session.execute(
                update(TelegramMessage)
                .where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.message_id == message_id,
                )
                .values(created_at=registered_at)
            )
        await session.commit()
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id, require_boundary=True)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.SESSION_START.value, "Начнём"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Цель удалена."),
        (MessageKind.DIALOGUE_USER.value, "Создай задачу подтянуться 20 раз"),
    ]


async def test_kind_marks_rebuild_history_without_registrations(sessions) -> None:
    """A rebuilt database loses every registration; Telegram must still be enough."""
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(
            14, "What should I do next?", owner_id, at + timedelta(days=1)
        ),
        FakeTelegramMessage(
            13, mark_kind("<b>Today</b>", MessageKind.DASHBOARD), bot_id, at + timedelta(minutes=3)
        ),
        FakeTelegramMessage(
            12,
            mark_kind("Stretching sounds good.", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at + timedelta(minutes=2),
        ),
        FakeTelegramMessage(11, "I want to stretch daily", owner_id, at + timedelta(minutes=1)),
        FakeTelegramMessage(10, "/newsession Let us begin", owner_id, at),
        FakeTelegramMessage(9, "Old unrelated private-chat message", owner_id, at - timedelta(1)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id, require_boundary=True)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.SESSION_START.value, "Let us begin"),
        (MessageKind.DIALOGUE_USER.value, "I want to stretch daily"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Stretching sounds good."),
        (MessageKind.DIALOGUE_USER.value, "What should I do next?"),
    ]


async def test_unregistered_owner_text_without_a_boundary_stays_excluded(sessions) -> None:
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(3, "Old unrelated private-chat message", owner_id, at),
        FakeTelegramMessage(2, "Another one", owner_id, at - timedelta(minutes=1)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    assert await source.recent(chat_id) == []


def test_kind_mark_round_trips_and_is_invisible() -> None:
    for kind in MessageKind:
        marked = mark_kind("Visible text", kind)
        assert marked.startswith("Visible text")
        assert read_kind_mark(marked) == (kind.value, "Visible text")
    assert read_kind_mark("Unmarked text") == (None, "Unmarked text")


async def test_unmarked_bot_prose_falls_back_to_a_plain_answer(sessions) -> None:
    """Messages written before kind marks existed are still readable as dialogue."""
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(24, "<b>Today</b>", bot_id, at + timedelta(minutes=3), object()),
        FakeTelegramMessage(23, "Safwa plans your week.", bot_id, at + timedelta(minutes=2)),
        FakeTelegramMessage(22, "How does Safwa work?", owner_id, at + timedelta(minutes=1)),
        FakeTelegramMessage(21, "/newsession Let us begin", owner_id, at),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id, require_boundary=True)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.SESSION_START.value, "Let us begin"),
        (MessageKind.DIALOGUE_USER.value, "How does Safwa work?"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Safwa plans your week."),
    ]
