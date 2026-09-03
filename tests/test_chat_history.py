from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from marks import mark_kind, mark_message, read_kind_mark
from sqlalchemy import update
from telethon.tl.types import MessageEntityTextUrl

from safwa.adapters.telegram_history import (
    TelegramHistorySource,
    TelegramMessage,
    register_message,
)
from safwa.ai.conversation import conversation_block
from safwa.bootstrap.modules import SCREENS
from safwa.dialogue_marks import MARKS, MessageKind
from safwa.features.continuity.model import SUMMARY_HEADER
from telegram_llm import DialogueMessage, HistoryEntry, restore_citations


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
    """Put every note at `moment`, so a test can place one after the messages it reads."""
    async with sessions() as session:
        await session.execute(
            update(TelegramMessage)
            .where(TelegramMessage.chat_id == chat_id)
            .values(created_at=moment)
        )
        await session.commit()


async def test_surviving_owner_text_is_what_the_owner_said(sessions) -> None:
    """TG-OWNER-003 — tests/brd/telegram_history.feature"""
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(9, "Hello Safwa", owner_id, at + timedelta(minutes=4)),
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
        FakeTelegramMessage(3, "An old message from the bot", bot_id, at - timedelta(hours=2)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_ASSISTANT.value, "Hello — how can I help?"),
        (MessageKind.DIALOGUE_USER.value, "Hello Safwa"),
    ]
    dialogue = await source.dialogue(chat_id)
    assert [message.role for message in dialogue] == ["assistant", "user"]


async def test_summary_is_pinned_first_with_twenty_prior_messages(sessions) -> None:
    """TG-SUMMARY-006 — tests/brd/telegram_history.feature"""
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
            mark_kind(f"{SUMMARY_HEADER}\nThe important earlier context.", MessageKind.SUMMARY),
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
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
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
    """TG-WINDOW-005 — tests/brd/telegram_history.feature"""
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
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id, token_budget=250)

    # Each message costs about 82 tokens, so three fit and the fourth is left whole.
    assert [entry.message_id for entry in entries] == [7, 8, 9]


async def test_the_answered_message_is_not_read_twice(sessions) -> None:
    """TG-CURRENT-008 — tests/brd/telegram_history.feature"""
    chat_id, owner_id, bot_id = 105, 42, 99
    at = datetime(2026, 8, 9, 12, 47, tzinfo=UTC)
    # The Bot API numbered the owner's message 13; a Telethon session calls it 95_003.
    messages = [
        FakeTelegramMessage(95_003, "Как дела?", owner_id, at + timedelta(seconds=2)),
        FakeTelegramMessage(
            95_002,
            mark_kind("Safwa answer", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at + timedelta(seconds=1),
        ),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
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
    assert dialogue[-1].content.count("Как дела?") == 1


async def test_dialogue_groups_every_user_message_until_the_next_ai_response(sessions) -> None:
    """TG-SHAPE-010 — tests/brd/telegram_history.feature"""
    source = TelegramHistorySource(None, sessions, marks=MARKS, bot_user_id=99, owner_id=42)
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


async def test_kind_marks_rebuild_history_without_registrations(sessions) -> None:
    """TG-NOTES-007 — tests/brd/telegram_history.feature"""
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
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_USER.value, "I want to stretch daily"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Stretching sounds good."),
        (MessageKind.DIALOGUE_USER.value, "What should I do next?"),
    ]


async def test_the_read_reaches_past_every_note_safwa_kept(sessions) -> None:
    """TG-NOTES-007 — tests/brd/telegram_history.feature"""
    chat_id, owner_id, bot_id = 114, 42, 99
    at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(72, "Newest thought", owner_id, at + timedelta(minutes=2)),
        FakeTelegramMessage(
            71,
            mark_kind("Older answer", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at - timedelta(hours=2),
        ),
        FakeTelegramMessage(70, "Older thought", owner_id, at - timedelta(hours=3)),
    ]
    # One note, newer than two of the three messages: the read used to stop at it.
    await register(sessions, chat_id, 900, "out", MessageKind.DASHBOARD)
    await backdate(sessions, chat_id, at + timedelta(minutes=1))
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id)

    assert [entry.text for entry in entries] == [
        "Older thought",
        "Older answer",
        "Newest thought",
    ]


async def test_a_summary_too_long_for_one_message_reads_as_one(sessions) -> None:
    """SC-SPLIT-004 — tests/brd/screens.feature"""
    chat_id, owner_id, bot_id = 115, 42, 99
    at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(83, "After the Summary", owner_id, at + timedelta(minutes=4)),
        FakeTelegramMessage(
            82,
            mark_kind("second half.", MessageKind.SUMMARY),
            bot_id,
            at + timedelta(minutes=3),
        ),
        FakeTelegramMessage(
            81,
            mark_kind(f"{SUMMARY_HEADER}\nFirst half,", MessageKind.SUMMARY),
            bot_id,
            at + timedelta(minutes=2),
        ),
        FakeTelegramMessage(80, "Before the Summary", owner_id, at + timedelta(minutes=1)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id)

    assert entries[0].kind == MessageKind.SUMMARY.value
    assert entries[0].text == "First half,\nsecond half."
    # The newest part is the cut place, which is what `record_summary` stored.
    assert entries[0].message_id == 82
    assert [entry.text for entry in entries[1:]] == [
        "Before the Summary",
        "After the Summary",
    ]


async def test_an_older_summary_with_only_a_screen_between_them_is_not_read(sessions) -> None:
    """TG-SUMMARY-006 — tests/brd/telegram_history.feature"""
    chat_id, owner_id, bot_id = 116, 42, 99
    at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(
            93,
            mark_kind(f"{SUMMARY_HEADER}\nThe newest Summary.", MessageKind.SUMMARY),
            bot_id,
            at + timedelta(minutes=4),
        ),
        FakeTelegramMessage(
            92,
            mark_kind("Today's workspace", MessageKind.DASHBOARD),
            bot_id,
            at + timedelta(minutes=3),
        ),
        FakeTelegramMessage(
            91,
            mark_kind(f"{SUMMARY_HEADER}\nAn older Summary.", MessageKind.SUMMARY),
            bot_id,
            at + timedelta(minutes=2),
        ),
        FakeTelegramMessage(90, "Before both", owner_id, at + timedelta(minutes=1)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id)

    # A dashboard between them is still a message, so these are two Summaries and not one
    # split in two. The older one is already represented by the newer.
    assert entries[0].kind == MessageKind.SUMMARY.value
    assert entries[0].text == "The newest Summary."
    assert [entry.text for entry in entries[1:]] == ["Before both"]


async def test_item_links_read_back_as_the_citations_the_model_wrote(sessions) -> None:
    """TG-CITE-011 — tests/brd/telegram_history.feature"""
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
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id)

    assert entries[-1].text == "🎯 Answer [Milk](check:14), then close [Market](card:12)."


def test_restore_citations_only_rewrites_safwa_deep_links() -> None:
    """TG-CITE-011 — tests/brd/telegram_history.feature"""
    assert restore_citations("Milk", None, SCREENS.types) == "Milk"
    assert (
        restore_citations(
            "Milk",
            [MessageEntityTextUrl(0, 4, "https://t.me/x?start=check-14")],
            SCREENS.types,
        )
        == "[Milk](check:14)"
    )
    # A link that is not an item link is left exactly as the user reads it.
    for url in ("https://t.me/x?start=sprint-1", "https://t.me/safwa_ai_bot", "https://ok.dev"):
        assert restore_citations("Milk", [MessageEntityTextUrl(0, 4, url)], SCREENS.types) == "Milk"


def test_a_compact_diary_label_round_trips_as_a_citation() -> None:
    """TG-CITE-011 — tests/brd/telegram_history.feature"""
    restored = restore_citations(
        "Тот день: 4 марта · 🙂6.",
        [MessageEntityTextUrl(10, 13, "https://t.me/x?start=diary-12")],
        SCREENS.types,
    )
    assert restored == "Тот день: [4 марта · 🙂6](diary:12)."
    match = SCREENS.citation.search(restored)
    assert match is not None
    assert (match[1], match[2], match[3]) == ("4 марта · 🙂6", "diary", "12")
    # An unrelated bracket pair still does not swallow the citation next to it.
    neighbour = SCREENS.citation.search("[note] and [Milk](check:14)")
    assert neighbour is not None and neighbour[1] == "Milk"


def test_kind_mark_round_trips_and_is_invisible() -> None:
    """TG-MARK-001 — tests/brd/telegram_history.feature"""
    for kind in MessageKind:
        marked = mark_kind("Visible text", kind)
        assert marked.startswith("Visible text")
        assert read_kind_mark(marked) == (kind.value, "Visible text")
    assert read_kind_mark("Unmarked text") == (None, "Unmarked text")


def test_event_marker_survives_message_edits() -> None:
    """TG-MARK-001 — tests/brd/telegram_history.feature"""
    original, event_id = mark_message("Original", MessageKind.DIALOGUE_ASSISTANT)
    edited = mark_kind("Edited", MessageKind.DIALOGUE_ASSISTANT, event_id=event_id)

    assert MARKS.read(original)[1] == event_id
    assert MARKS.read(edited) == (
        MessageKind.DIALOGUE_ASSISTANT.value,
        event_id,
        "Edited",
    )


async def test_a_receipt_reads_back_as_a_tool_result_rather_than_as_safwa_words(
    sessions,
) -> None:
    """TG-RECEIPT-009 — tests/brd/telegram_history.feature"""
    source = TelegramHistorySource(None, sessions, marks=MARKS, bot_user_id=99, owner_id=42)
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
    """TG-MARK-001 — tests/brd/telegram_history.feature"""
    chat_id, owner_id, bot_id = 100, 42, 99
    at = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(24, "<b>Today</b>", bot_id, at + timedelta(minutes=3), object()),
        FakeTelegramMessage(23, "Safwa plans your week.", bot_id, at + timedelta(minutes=2)),
        FakeTelegramMessage(22, "How does Safwa work?", owner_id, at + timedelta(minutes=1)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_USER.value, "How does Safwa work?"),
    ]


async def test_screens_receipts_and_progress_notes_are_not_the_conversation(sessions) -> None:
    """TG-KIND-002 — tests/brd/telegram_history.feature"""
    chat_id, owner_id, bot_id = 110, 42, 99
    at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(
            37,
            mark_kind("Your Sprint ended.", MessageKind.CUE),
            bot_id,
            at + timedelta(minutes=7),
        ),
        FakeTelegramMessage(
            36,
            mark_kind("Safwa could not complete that request.", MessageKind.ERROR),
            bot_id,
            at + timedelta(minutes=6),
        ),
        FakeTelegramMessage(
            35,
            mark_kind("\U0001f3a7 Transcribing... 40%", MessageKind.STATUS),
            bot_id,
            at + timedelta(minutes=5),
        ),
        FakeTelegramMessage(
            34,
            mark_kind("Saved. Safwa is continuing...", MessageKind.RECEIPT),
            bot_id,
            at + timedelta(minutes=4),
        ),
        FakeTelegramMessage(
            33,
            mark_kind("<b>Today</b>", MessageKind.DASHBOARD),
            bot_id,
            at + timedelta(minutes=3),
        ),
        FakeTelegramMessage(
            32,
            mark_kind("Good idea.", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at + timedelta(minutes=2),
        ),
        FakeTelegramMessage(31, "I want to stretch daily", owner_id, at + timedelta(minutes=1)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_USER.value, "I want to stretch daily"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Good idea."),
        (MessageKind.CUE.value, "Your Sprint ended."),
    ]


async def test_a_command_left_standing_in_the_chat_is_still_not_the_conversation(
    sessions,
) -> None:
    """TG-OWNER-003 — tests/brd/telegram_history.feature"""
    chat_id, owner_id, bot_id = 111, 42, 99
    at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(43, "What should I do next?", owner_id, at + timedelta(minutes=3)),
        FakeTelegramMessage(42, "/today", owner_id, at + timedelta(minutes=2)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    entries = await source.recent(chat_id)

    assert [entry.text for entry in entries] == ["What should I do next?"]


async def test_two_answers_with_nothing_between_them_read_as_one(sessions) -> None:
    """TG-SHAPE-010 — tests/brd/telegram_history.feature"""
    source = TelegramHistorySource(None, sessions, marks=MARKS, bot_user_id=99, owner_id=42)
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    entries = [
        HistoryEntry(1, 42, "user", "What is left today?", at, "dialogue_user"),
        HistoryEntry(2, 99, "assistant", "Two Actions.", at, "dialogue_assistant"),
        HistoryEntry(3, 99, "assistant", "Both are short.", at, "dialogue_assistant"),
    ]

    async def recent(*_args, **_kwargs):
        return entries

    source.recent = recent  # type: ignore[method-assign]
    dialogue = await source.dialogue(100)

    assert [(item.role, item.content) for item in dialogue] == [
        ("user", "[2026-08-08 12:00] [User]: What is left today?"),
        ("assistant", "Two Actions.\nBoth are short."),
    ]


async def test_the_answered_message_is_added_when_the_chat_read_has_not_caught_up(
    sessions,
) -> None:
    """TG-CURRENT-008 — tests/brd/telegram_history.feature"""
    chat_id, owner_id, bot_id = 112, 42, 99
    at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(
            51,
            mark_kind("Earlier answer", MessageKind.DIALOGUE_ASSISTANT),
            bot_id,
            at + timedelta(minutes=1),
        ),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )
    current = HistoryEntry(
        message_id=52,
        sender_id=owner_id,
        role="user",
        text="And now?",
        created_at=at + timedelta(minutes=2),
        kind=MessageKind.DIALOGUE_USER.value,
    )

    entries = await source.recent(chat_id, source_message=current)

    assert [entry.text for entry in entries] == ["Earlier answer", "And now?"]


async def test_reading_one_named_day_reads_past_the_summary_inside_it(sessions) -> None:
    """DI-READ-016 — tests/brd/diary.feature"""
    chat_id, owner_id, bot_id = 113, 42, 99
    day = datetime(2026, 8, 14, tzinfo=UTC)
    messages = [
        FakeTelegramMessage(64, "The day after", owner_id, day + timedelta(hours=25)),
        FakeTelegramMessage(63, "Evening thought", owner_id, day + timedelta(hours=20)),
        FakeTelegramMessage(
            62,
            mark_kind("\U0001f4dc Summary\nThe morning, in short.", MessageKind.SUMMARY),
            bot_id,
            day + timedelta(hours=13),
        ),
        FakeTelegramMessage(61, "Morning thought", owner_id, day + timedelta(hours=8)),
        FakeTelegramMessage(60, "The day before", owner_id, day - timedelta(hours=1)),
    ]
    source = TelegramHistorySource(
        FakeTelegramClient(messages), sessions,
            marks=MARKS,
            bot_user_id=bot_id,
            owner_id=owner_id,
            citation_types=SCREENS.types,
    )

    transcript = await source.day_transcript(
        chat_id, start=day, end=day + timedelta(days=1), token_budget=6_000
    )

    assert transcript == "[08:00] [User]: Morning thought\n[20:00] [User]: Evening thought"


def test_conversation_block_tags_every_line_by_who_wrote_it() -> None:
    """A reader that took no part in the conversation gets it as data, not as its turns."""
    block = conversation_block(
        [
            DialogueMessage(
                role="user",
                content=(
                    "[2026-08-08 12:00] [User]: Создай действие убраться в комнате\n"
                    "[Tool result]: New Action “Убраться” (Backlog · 2 EP) — applied"
                ),
            ),
            DialogueMessage(role="assistant", content="Готово!\nЧто дальше?"),
        ]
    )

    assert block == (
        "<Conversation>\n"
        '<User at="2026-08-08 12:00">Создай действие убраться в комнате</User>\n'
        "<ToolResult>New Action “Убраться” (Backlog · 2 EP) — applied</ToolResult>\n"
        "<Advisor>Готово!\nЧто дальше?</Advisor>\n"
        "</Conversation>"
    )


def test_conversation_block_is_empty_when_the_conversation_is() -> None:
    assert conversation_block([]) == ""
