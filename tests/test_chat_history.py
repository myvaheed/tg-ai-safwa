from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from telegram_fakes import spawn_timer
from ui_harness import FakeMessage, history_source

from safwa.bootstrap.modules import SCREENS
from safwa.features.summary.window import SUMMARY_HEADER
from telegram_llm import ChatHost, DialogueMessage, HistoryEntry, Note, telegram_html_to_text
from tg_agent_shell.ai.conversation import conversation_block
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.telegram import SHELL_COMMANDS
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.routing import build_router


async def keep(
    sessions, chat_id: int, message_id: int, text: str, kind: MessageKind, at: datetime
) -> None:
    """One message as the bot keeps it: the person's words come in, everything else goes out."""
    direction = "in" if kind is MessageKind.DIALOGUE_USER else "out"
    await TelegramNotes(sessions).write(
        Note(
            chat_id=chat_id,
            message_id=message_id,
            direction=direction,
            kind=kind.value,
            text=text,
            at=at,
        )
    )


def owner_message(message_id: int, text: str, chat_id: int) -> FakeMessage:
    return FakeMessage(message_id, text=text, bot_message=False, chat_id=chat_id)


def next_in_chat(message_id: int, chat_id: int) -> FakeMessage:
    """Where the bot's next message lands: Telegram numbers it `message_id`."""
    return FakeMessage(message_id, bot_message=True, chat_id=chat_id)


async def test_the_owner_s_words_are_kept_and_a_field_value_is_not(sessions) -> None:
    """TG-OWNER-003 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 100
    host = ChatHost(TelegramNotes(sessions), spawn=spawn_timer)
    await host.keep(owner_message(9, "Hello Safwa", chat_id), kind=MessageKind.DIALOGUE_USER.value)
    await host.remove_incoming(owner_message(10, "42", chat_id), kind=MessageKind.UI_INPUT.value)
    await host.send(
        next_in_chat(11, chat_id), "Hello — how can I help?",
        kind=MessageKind.DIALOGUE_ASSISTANT.value, replace=False,
    )

    entries = await history_source(sessions).recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_USER.value, "Hello Safwa"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Hello — how can I help?"),
    ]


async def test_text_opening_with_a_slash_never_reaches_the_conversation() -> None:
    """TG-OWNER-003 — tests/brd/tg_agent_shell/telegram_history.feature"""
    router = build_router(SHELL_COMMANDS)
    conversation = next(
        handler for handler in router.message.handlers if handler.callback is ordinary_text
    )

    assert (await conversation.check(SimpleNamespace(text="What should I do next?")))[0]
    assert not (await conversation.check(SimpleNamespace(text="/unknown")))[0]


async def test_summary_is_pinned_first_with_twenty_prior_messages(sessions) -> None:
    """TG-SUMMARY-006 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 101
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    for message_id in range(68, 98):
        await keep(
            sessions, chat_id, message_id, f"Older Safwa dialogue {message_id}",
            MessageKind.DIALOGUE_USER, at + timedelta(minutes=message_id),
        )
    await keep(
        sessions, chat_id, 98, f"{SUMMARY_HEADER}\nThe important earlier context.",
        MessageKind.SUMMARY, at + timedelta(minutes=98),
    )
    await keep(
        sessions, chat_id, 99, "Recent Safwa answer",
        MessageKind.DIALOGUE_ASSISTANT, at + timedelta(minutes=99),
    )
    await keep(
        sessions, chat_id, 100, "Current turn", MessageKind.DIALOGUE_USER,
        at + timedelta(minutes=100),
    )
    source = history_source(sessions)

    entries = await source.recent(chat_id)

    assert entries[0].kind == MessageKind.SUMMARY.value
    assert entries[0].text == "[Summary]: The important earlier context."
    assert [entry.message_id for entry in entries[1:21]] == list(range(78, 98))
    assert all(entry.before_edge for entry in entries[1:21])
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
    """TG-WINDOW-005 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 107
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    for message_id in range(1, 10):
        await keep(
            sessions, chat_id, message_id, f"{message_id} " + "word " * 40,
            MessageKind.DIALOGUE_USER, at + timedelta(minutes=message_id),
        )

    entries = await history_source(sessions).recent(chat_id, token_budget=250)

    # Each message costs about 82 tokens, so three fit and the fourth is left whole.
    assert [entry.message_id for entry in entries] == [7, 8, 9]


async def test_the_answered_message_is_read_exactly_once(sessions) -> None:
    """TG-CURRENT-008 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 105
    at = datetime(2026, 8, 9, 12, 47, tzinfo=UTC)
    await keep(sessions, chat_id, 12, "Safwa answer", MessageKind.DIALOGUE_ASSISTANT, at)
    host = ChatHost(TelegramNotes(sessions), spawn=spawn_timer)
    current = owner_message(13, "Как дела?", chat_id)
    # The handler keeps the words before it reads, so the answer finds them in the chat.
    await host.keep(current, kind=MessageKind.DIALOGUE_USER.value)

    dialogue = await history_source(sessions).dialogue(chat_id)

    assert dialogue[-1].role == "user"
    assert dialogue[-1].content.endswith("[User]: Как дела?")
    assert dialogue[-1].content.count("Как дела?") == 1


async def test_dialogue_groups_every_user_message_until_the_next_ai_response(sessions) -> None:
    """TG-SHAPE-010 — tests/brd/tg_agent_shell/telegram_history.feature"""
    source = history_source(sessions)
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    entries = [
        HistoryEntry(1, "user", "First thought", at, "dialogue_user"),
        HistoryEntry(2, "user", "Second thought", at, "dialogue_user"),
        HistoryEntry(3, "assistant", "Safwa reply", at, "dialogue_assistant"),
        HistoryEntry(4, "user", "Follow-up", at + timedelta(hours=1), "dialogue_user"),
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


async def test_the_conversation_is_what_safwa_kept_and_not_what_the_chat_shows(sessions) -> None:
    """TG-NOTES-007 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 114
    host = ChatHost(TelegramNotes(sessions), spawn=spawn_timer)
    # The owner later deletes this one from their own chat; nothing tells the bot.
    await host.keep(
        owner_message(70, "Older thought", chat_id), kind=MessageKind.DIALOGUE_USER.value
    )
    await host.send(
        next_in_chat(71, chat_id), "Older answer",
        kind=MessageKind.DIALOGUE_ASSISTANT.value, replace=False,
    )
    screen = await host.send(
        next_in_chat(72, chat_id), "Stretch · 2 EP",
        kind=MessageKind.DIALOGUE_ASSISTANT.value, replace=False,
    )
    # Safwa takes this one out of the chat itself, so it goes from what was kept too.
    await host.remove_screen(screen, screen.message_id)
    await host.keep(
        owner_message(73, "Newest thought", chat_id), kind=MessageKind.DIALOGUE_USER.value
    )

    entries = await history_source(sessions).recent(chat_id)

    assert [entry.text for entry in entries] == ["Older thought", "Older answer", "Newest thought"]


async def test_an_edit_the_owner_makes_is_what_the_conversation_reads(sessions) -> None:
    """TG-EDIT-012 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 117
    host = ChatHost(TelegramNotes(sessions), spawn=spawn_timer)
    await host.keep(owner_message(20, "Buy milk", chat_id), kind=MessageKind.DIALOGUE_USER.value)
    await host.send(
        next_in_chat(21, chat_id), "Noted.",
        kind=MessageKind.DIALOGUE_ASSISTANT.value, replace=False,
    )
    await host.remove_incoming(owner_message(22, "3", chat_id), kind=MessageKind.UI_INPUT.value)

    await host.amend(owner_message(20, "Buy milk & eggs", chat_id))
    await host.amend(owner_message(22, "5", chat_id))

    entries = await history_source(sessions).recent(chat_id)

    assert [entry.text for entry in entries] == ["Buy milk & eggs", "Noted."]


async def test_a_summary_too_long_for_one_message_reads_as_one(sessions) -> None:
    """SC-SPLIT-004 — tests/brd/tg_agent_shell/screens.feature"""
    chat_id = 115
    at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    await keep(sessions, chat_id, 80, "Before the Summary", MessageKind.DIALOGUE_USER, at)
    await keep(
        sessions, chat_id, 81, f"{SUMMARY_HEADER}\nFirst half,", MessageKind.SUMMARY,
        at + timedelta(minutes=1),
    )
    await keep(sessions, chat_id, 82, "second half.", MessageKind.SUMMARY, at + timedelta(minutes=2))
    await keep(
        sessions, chat_id, 83, "After the Summary", MessageKind.DIALOGUE_USER,
        at + timedelta(minutes=3),
    )

    entries = await history_source(sessions).recent(chat_id)

    assert entries[0].kind == MessageKind.SUMMARY.value
    assert entries[0].text == "[Summary]: First half,\nsecond half."
    # The newest part is where the backwards read meets the Summary, so it is the cut.
    assert entries[0].message_id == 82
    assert [entry.text for entry in entries[1:]] == ["Before the Summary", "After the Summary"]


async def test_an_older_summary_with_only_a_screen_between_them_is_not_read(sessions) -> None:
    """TG-SUMMARY-006 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 116
    at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    await keep(sessions, chat_id, 90, "Before both", MessageKind.DIALOGUE_USER, at)
    await keep(
        sessions, chat_id, 91, f"{SUMMARY_HEADER}\nAn older Summary.", MessageKind.SUMMARY,
        at + timedelta(minutes=1),
    )
    await keep(
        sessions, chat_id, 92, "Today's workspace", MessageKind.DASHBOARD,
        at + timedelta(minutes=2),
    )
    await keep(
        sessions, chat_id, 93, f"{SUMMARY_HEADER}\nThe newest Summary.", MessageKind.SUMMARY,
        at + timedelta(minutes=3),
    )

    entries = await history_source(sessions).recent(chat_id)

    # A dashboard between them is still a message, so these are two Summaries and not one
    # split in two. The older one is already represented by the newer.
    assert entries[0].kind == MessageKind.SUMMARY.value
    assert entries[0].text == "[Summary]: The newest Summary."
    assert [entry.text for entry in entries[1:]] == ["Before both"]


async def test_item_links_read_back_as_the_citations_the_model_wrote(sessions) -> None:
    """TG-CITE-011 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 100
    reply = (
        '🎯 Answer <a href="https://t.me/safwa_ai_bot?start=check-14">Milk</a>, then close '
        '<b><a href="https://t.me/safwa_ai_bot?start=card-12">Market</a></b> '
        '&amp; <a href="https://example.com/not-safwa">read this</a>.'
    )
    await keep(
        sessions, chat_id, 21, reply, MessageKind.DIALOGUE_ASSISTANT,
        datetime(2026, 8, 13, 12, 1, tzinfo=UTC),
    )

    entries = await history_source(sessions).recent(chat_id)

    assert entries[-1].text == (
        "🎯 Answer [Milk](check:14), then close [Market](card:12) & read this."
    )


def test_only_safwa_item_links_are_written_back_as_citations() -> None:
    """TG-CITE-011 — tests/brd/tg_agent_shell/telegram_history.feature"""
    assert telegram_html_to_text("Milk", SCREENS.types) == "Milk"
    assert (
        telegram_html_to_text('<a href="https://t.me/x?start=check-14">Milk</a>', SCREENS.types)
        == "[Milk](check:14)"
    )
    # A link that is not an item link reads as the words the user sees.
    for url in ("https://t.me/x?start=sprint-1", "https://t.me/safwa_ai_bot", "https://ok.dev"):
        assert telegram_html_to_text(f'<a href="{url}">Milk</a>', SCREENS.types) == "Milk"


def test_a_compact_diary_label_round_trips_as_a_citation() -> None:
    """TG-CITE-011 — tests/brd/tg_agent_shell/telegram_history.feature"""
    restored = telegram_html_to_text(
        'Тот день: <a href="https://t.me/x?start=diary-12">4 марта · 🙂6</a>.', SCREENS.types
    )
    assert restored == "Тот день: [4 марта · 🙂6](diary:12)."
    match = SCREENS.citation.search(restored)
    assert match is not None
    assert (match[1], match[2], match[3]) == ("4 марта · 🙂6", "diary", "12")
    # An unrelated bracket pair still does not swallow the citation next to it.
    neighbour = SCREENS.citation.search("[note] and [Milk](check:14)")
    assert neighbour is not None and neighbour[1] == "Milk"


async def test_a_screen_rewritten_in_place_stays_one_kept_message(sessions) -> None:
    """TG-MARK-001 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 118
    host = ChatHost(TelegramNotes(sessions), spawn=spawn_timer)
    anchor = FakeMessage(30, bot_message=False, chat_id=chat_id, answer_as_new=True)
    screen = await host.send(
        anchor, "<b>Review</b>\nNew Action “Stretch”", kind=MessageKind.APPROVAL.value,
        replace=False,
    )
    drawn = await TelegramNotes(sessions).note(chat_id, screen.message_id)
    assert drawn is not None and drawn.text is not None

    assert await history_source(sessions).recent(chat_id) == []

    await host.freeze_screen(
        anchor, drawn, "Request interrupted: nothing was saved.",
        MessageKind.DIALOGUE_ASSISTANT.value,
    )

    frozen = await TelegramNotes(sessions).note(chat_id, screen.message_id)
    assert frozen is not None
    assert (frozen.kind, frozen.event_id) == (MessageKind.DIALOGUE_ASSISTANT.value, drawn.event_id)
    entries = await history_source(sessions).recent(chat_id)
    assert [(entry.message_id, entry.text) for entry in entries] == [
        (screen.message_id, "Request interrupted: nothing was saved.")
    ]


async def test_a_receipt_reads_back_as_a_tool_result_rather_than_as_safwa_words(
    sessions,
) -> None:
    """TG-RECEIPT-009 — tests/brd/tg_agent_shell/telegram_history.feature"""
    source = history_source(sessions)
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    entries = [
        HistoryEntry(1, "user", "Создай действие убраться в комнате", at, "dialogue_user"),
        HistoryEntry(
            2,
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


async def test_screens_receipts_and_progress_notes_are_not_the_conversation(sessions) -> None:
    """TG-KIND-002 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 110
    at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    kept = [
        (31, "I want to stretch daily", MessageKind.DIALOGUE_USER),
        (32, "Good idea.", MessageKind.DIALOGUE_ASSISTANT),
        (33, "<b>Today</b>", MessageKind.DASHBOARD),
        (34, "Saved. Continuing...", MessageKind.RECEIPT),
        (35, "\U0001f3a7 Transcribing... 40%", MessageKind.STATUS),
        (36, "That request could not be completed.", MessageKind.ERROR),
        (37, "Your Sprint ended.", MessageKind.CUE),
    ]
    for minute, (message_id, text, kind) in enumerate(kept):
        await keep(sessions, chat_id, message_id, text, kind, at + timedelta(minutes=minute))

    entries = await history_source(sessions).recent(chat_id)

    assert [(entry.kind, entry.text) for entry in entries] == [
        (MessageKind.DIALOGUE_USER.value, "I want to stretch daily"),
        (MessageKind.DIALOGUE_ASSISTANT.value, "Good idea."),
        (MessageKind.CUE.value, "Your Sprint ended."),
    ]


async def test_two_answers_with_nothing_between_them_read_as_one(sessions) -> None:
    """TG-SHAPE-010 — tests/brd/tg_agent_shell/telegram_history.feature"""
    source = history_source(sessions)
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    entries = [
        HistoryEntry(1, "user", "What is left today?", at, "dialogue_user"),
        HistoryEntry(2, "assistant", "Two Actions.", at, "dialogue_assistant"),
        HistoryEntry(3, "assistant", "Both are short.", at, "dialogue_assistant"),
    ]

    async def recent(*_args, **_kwargs):
        return entries

    source.recent = recent  # type: ignore[method-assign]
    dialogue = await source.dialogue(100)

    assert [(item.role, item.content) for item in dialogue] == [
        ("user", "[2026-08-08 12:00] [User]: What is left today?"),
        ("assistant", "Two Actions.\nBoth are short."),
    ]


async def test_reading_one_named_day_reads_past_the_summary_inside_it(sessions) -> None:
    """DI-READ-016 — tests/brd/diary.feature"""
    chat_id = 113
    day = datetime(2026, 8, 14, tzinfo=UTC)
    kept = [
        (60, "The day before", MessageKind.DIALOGUE_USER, day - timedelta(hours=1)),
        (61, "Morning thought", MessageKind.DIALOGUE_USER, day + timedelta(hours=8)),
        (
            62, f"{SUMMARY_HEADER}\nThe morning, in short.", MessageKind.SUMMARY,
            day + timedelta(hours=13),
        ),
        (63, "Evening thought", MessageKind.DIALOGUE_USER, day + timedelta(hours=20)),
        (64, "The day after", MessageKind.DIALOGUE_USER, day + timedelta(hours=25)),
    ]
    for message_id, text, kind, at in kept:
        await keep(sessions, chat_id, message_id, text, kind, at)

    transcript = await history_source(sessions).day_transcript(
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
