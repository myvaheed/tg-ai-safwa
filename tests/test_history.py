from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from telethon.tl.types import MessageEntityTextUrl

from safwa.enums import MessageKind
from safwa.history import (
    CITATION_PATTERN,
    HistoryEntry,
    TelegramHistorySource,
    mark_kind,
    mark_message,
    read_kind_mark,
    read_message_mark,
    register_message,
    restore_citations,
)
from safwa.models import TelegramMessage


@dataclass
class FakeTelegramMessage:
    id: int
    raw_text: str
    sender_id: int
    date: datetime
    reply_markup: object | None = None
    entities: list[object] | None = None


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


async def backdate(sessions, chat_id: int, moment: datetime) -> None:
    """Put every registration at `moment`, which is what the read floor is taken from."""
    async with sessions() as session:
        await session.execute(
            update(TelegramMessage)
            .where(TelegramMessage.chat_id == chat_id)
            .values(created_at=moment)
        )
        await session.commit()


async def test_surviving_owner_text_is_dialogue_down_to_the_oldest_registration(
    sessions,
) -> None:
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(9, "Hello Safwa", owner_id, at + timedelta(minutes=4)),
        FakeTelegramMessage(
            8, "A card title entered in a form", owner_id, at + timedelta(minutes=3)
        ),
        FakeTelegramMessage(
            7,
            mark_kind("Hello — how can I help?", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at + timedelta(minutes=2),
        ),
        FakeTelegramMessage(
            6,
            mark_kind("<b>Today</b>", MessageKind.DASHBOARD),
            bot_id,
            at + timedelta(minutes=1),
        ),
        FakeTelegramMessage(
            4, "Old unrelated private-chat message", owner_id, at - timedelta(hours=1)
        ),
        FakeTelegramMessage(3, "An old message from the bot", bot_id, at - timedelta(hours=2)),
    ]
    await register(sessions, chat_id, 9, "in", MessageKind.DIALOGUE_USER)
    await register(sessions, chat_id, 8, "in", MessageKind.UI_INPUT)
    await register(sessions, chat_id, 7, "out", MessageKind.DIALOGUE_ASSISTANT)
    await register(sessions, chat_id, 6, "out", MessageKind.DASHBOARD)
    await backdate(sessions, chat_id, at + timedelta(minutes=1))
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_ASSISTANT.value, "Hello — how can I help?"),
        (MessageKind.DIALOGUE_USER.value, "Hello Safwa"),
    ]
    dialogue = await source.dialogue(chat_id)
    assert [message.role for message in dialogue] == ["assistant", "user"]


async def test_summary_is_pinned_first_with_twenty_prior_messages(sessions) -> None:
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
        FakeTelegramMessage(
            99,
            mark_kind("Recent Safwa answer", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at + timedelta(minutes=99),
        ),
        FakeTelegramMessage(
            98,
            mark_kind("📜 Summary\nThe important earlier context.", MessageKind.SUMMARY),
            bot_id,
            at + timedelta(minutes=98),
        ),
        *older,
    ]
    await register(sessions, chat_id, 100, "in", MessageKind.DIALOGUE_USER)
    await register(sessions, chat_id, 99, "out", MessageKind.DIALOGUE_ASSISTANT)
    await register(sessions, chat_id, 98, "out", MessageKind.SUMMARY)
    for message in older:
        await register(sessions, chat_id, message.id, "in", MessageKind.DIALOGUE_USER)
    await backdate(sessions, chat_id, at)
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
    assert dialogue[0].content.startswith("[2026-08-08 13:38] [Summary]: The important")
    assert "\n[User]: Older Safwa dialogue 78" in dialogue[0].content
    # The whole window falls inside one hour, so it carries exactly one stamp.
    assert dialogue[0].content.count("[2026-08-08") == 1
    assert dialogue[-2].role == "assistant"
    assert dialogue[-1].content == "[User]: Current turn"


async def test_the_window_is_cut_on_a_message_boundary_when_the_budget_runs_out(
    sessions,
) -> None:
    chat_id, owner_id, bot_id = 107, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(
            message_id,
            f"{message_id} " + "word " * 40,
            owner_id,
            at + timedelta(minutes=message_id),
        )
        for message_id in range(9, 0, -1)
    ]
    for message in messages:
        await register(sessions, chat_id, message.id, "in", MessageKind.DIALOGUE_USER)
    await backdate(sessions, chat_id, at)
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id, token_budget=250)

    # Each message costs about 82 tokens, so three fit and the fourth is left whole.
    assert [entry.message_id for entry in entries] == [7, 8, 9]


async def test_private_chat_correlates_telethon_and_bot_api_message_ids(sessions) -> None:
    chat_id, owner_id, bot_id = 104, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(95_003, "Continue", owner_id, at + timedelta(seconds=2)),
        FakeTelegramMessage(
            95_002,
            mark_kind("Safwa answer", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at + timedelta(seconds=1),
        ),
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

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_ASSISTANT.value, "Safwa answer"),
        (MessageKind.DIALOGUE_USER.value, "Continue"),
    ]


async def test_current_source_is_not_duplicated_across_telegram_id_spaces(sessions) -> None:
    chat_id, owner_id, bot_id = 105, 42, 99
    at = datetime(2026, 8, 9, 12, 47, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(95_003, "Как дела?", owner_id, at + timedelta(seconds=2)),
        FakeTelegramMessage(
            95_002,
            mark_kind("Safwa answer", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at + timedelta(seconds=1),
        ),
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
    assert dialogue[-1].content.endswith("[User]: Как дела?")


async def test_owner_text_older_than_every_registration_is_excluded(sessions) -> None:
    chat_id, owner_id, bot_id = 102, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(3, "Привет", owner_id, at),
        FakeTelegramMessage(2, "Old unrelated text", owner_id, at - timedelta(hours=1)),
        FakeTelegramMessage(1, "Old bot UI", bot_id, at - timedelta(hours=2)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )
    await register(sessions, chat_id, 3, "in", MessageKind.DIALOGUE_USER)
    await backdate(sessions, chat_id, at)

    entries = await source.recent(chat_id)

    assert [entry.text for entry in entries] == ["Привет"]


async def test_dialogue_groups_every_user_message_until_the_next_ai_response(sessions) -> None:
    source = TelegramHistorySource(None, sessions, bot_user_id=99, owner_id=42)
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    entries = [
        HistoryEntry(1, 42, "user", "First thought", at, "dialogue_user"),
        HistoryEntry(2, 42, "user", "Second thought", at, "dialogue_user"),
        HistoryEntry(3, 99, "assistant", "Safwa reply", at, "dialogue_assistant"),
        HistoryEntry(4, 42, "user", "Follow-up", at + timedelta(hours=1), "dialogue_user"),
    ]

    async def recent(*_args, **_kwargs):
        return entries

    source.recent = recent  # type: ignore[method-assign]
    dialogue = await source.dialogue(100)

    # One stamp per hour of conversation, not one per message.
    assert [(item.role, item.content) for item in dialogue] == [
        ("user", "[2026-08-08 12:00] [User]: First thought\n[User]: Second thought"),
        ("assistant", "Safwa reply"),
        ("user", "[2026-08-08 13:00] [User]: Follow-up"),
    ]


async def test_colliding_id_space_does_not_drop_the_newest_dialogue_message(sessions) -> None:
    """A stale registration that happens to share a Telethon ID must not steal the match."""
    chat_id, owner_id, bot_id = 106, 42, 99
    at = datetime(2026, 8, 11, 21, 33, 33, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(406, "Создай задачу подтянуться 20 раз", owner_id, at),
        FakeTelegramMessage(
            405,
            mark_kind("Цель удалена.", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at - timedelta(seconds=30),
        ),
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

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_ASSISTANT.value, "Цель удалена."),
        (MessageKind.DIALOGUE_USER.value, "Создай задачу подтянуться 20 раз"),
    ]


async def test_kind_marks_rebuild_history_without_registrations(sessions) -> None:
    """A rebuilt database loses every registration; Telegram must still be enough."""
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(14, "What should I do next?", owner_id, at + timedelta(days=1)),
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
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_USER.value, "I want to stretch daily"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Stretching sounds good."),
        (MessageKind.DIALOGUE_USER.value, "What should I do next?"),
    ]


async def test_item_links_read_back_as_the_citations_the_model_wrote(sessions) -> None:
    """Telethon hands us plain text, so a rendered link has to be un-rendered here."""
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    reply = "🎯 Answer Milk, then close Market."
    messages = [
        FakeTelegramMessage(
            21,
            mark_kind(reply, MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at + timedelta(minutes=1),
            entities=[
                # Offsets count UTF-16 units, so the leading emoji shifts them by two.
                MessageEntityTextUrl(10, 4, "https://t.me/safwa_ai_bot?start=check-14"),
                MessageEntityTextUrl(27, 6, "https://t.me/safwa_ai_bot?start=card-12"),
                MessageEntityTextUrl(0, 2, "https://example.com/not-safwa"),
            ],
        ),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id)

    assert entries[-1].text == "🎯 Answer [Milk](check:14), then close [Market](card:12)."


def test_restore_citations_only_rewrites_safwa_deep_links() -> None:
    assert restore_citations("Milk", None) == "Milk"
    assert (
        restore_citations("Milk", [MessageEntityTextUrl(0, 4, "https://t.me/x?start=check-14")])
        == "[Milk](check:14)"
    )
    # A link that is not an item link is left exactly as the user reads it.
    for url in ("https://t.me/x?start=sprint-1", "https://t.me/safwa_ai_bot", "https://ok.dev"):
        assert restore_citations("Milk", [MessageEntityTextUrl(0, 4, url)]) == "Milk"


def test_a_compact_diary_label_round_trips_as_a_citation() -> None:
    restored = restore_citations(
        "Тот день: 4 марта · 🙂6.",
        [MessageEntityTextUrl(10, 13, "https://t.me/x?start=diary-12")],
    )
    assert restored == "Тот день: [4 марта · 🙂6](diary:12)."
    match = CITATION_PATTERN.search(restored)
    assert match is not None
    assert (match[1], match[2], match[3]) == ("4 марта · 🙂6", "diary", "12")
    # An unrelated bracket pair still does not swallow the citation next to it.
    neighbour = CITATION_PATTERN.search("[note] and [Milk](check:14)")
    assert neighbour is not None and neighbour[1] == "Milk"


def test_kind_mark_round_trips_and_is_invisible() -> None:
    for kind in MessageKind:
        marked = mark_kind("Visible text", kind)
        assert marked.startswith("Visible text")
        assert read_kind_mark(marked) == (kind.value, "Visible text")
    assert read_kind_mark("Unmarked text") == (None, "Unmarked text")


def test_event_marker_survives_message_edits() -> None:
    original, event_id = mark_message("Original", MessageKind.DIALOGUE_ASSISTANT)
    edited = mark_kind("Edited", MessageKind.DIALOGUE_ASSISTANT, event_id=event_id)

    assert read_message_mark(original)[1] == event_id
    assert read_message_mark(edited) == (
        MessageKind.DIALOGUE_ASSISTANT.value,
        event_id,
        "Edited",
    )


async def test_a_receipt_reads_back_as_a_tool_result_rather_than_as_safwa_words(
    sessions,
) -> None:
    """The interface's receipt is the owner's channel; only Safwa's prose stays assistant."""
    source = TelegramHistorySource(None, sessions, bot_user_id=99, owner_id=42)
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    entries = [
        HistoryEntry(1, 42, "user", "Создай действие убраться в комнате", at, "dialogue_user"),
        HistoryEntry(
            2,
            99,
            "assistant",
            "✅ Saved — New Action “Убраться в комнате” (Backlog · 2 EP)\n\nГотово!",
            at,
            "dialogue_assistant",
        ),
    ]

    async def recent(*_args, **_kwargs):
        return entries

    source.recent = recent  # type: ignore[method-assign]
    dialogue = await source.dialogue(100)

    assert [(item.role, item.content) for item in dialogue] == [
        (
            "user",
            "[2026-08-08 12:00] [User]: Создай действие убраться в комнате\n"
            "[Tool result]: New Action “Убраться в комнате” (Backlog · 2 EP) — applied",
        ),
        ("assistant", "Готово!"),
    ]


async def test_unmarked_bot_prose_is_excluded(sessions) -> None:
    """First-version history accepts bot dialogue only when Safwa marked it."""
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(24, "<b>Today</b>", bot_id, at + timedelta(minutes=3), object()),
        FakeTelegramMessage(23, "Safwa plans your week.", bot_id, at + timedelta(minutes=2)),
        FakeTelegramMessage(22, "How does Safwa work?", owner_id, at + timedelta(minutes=1)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions, bot_user_id=bot_id, owner_id=owner_id
    )

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_USER.value, "How does Safwa work?"),
    ]
