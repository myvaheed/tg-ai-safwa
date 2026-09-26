from __future__ import annotations

import html
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from telegram_fakes import spawn_timer
from ui_harness import FakeMessage, history_source

from safwa.bootstrap.modules import SCREENS
from safwa.features.summary.window import SUMMARY_HEADER
from telegram_llm import ChatHost, HistoryEntry, Note, telegram_html_to_text
from tg_agent_shell.ai.conversation import CLEARED_READ, conversation_block, kept_turn
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.telegram import SHELL_COMMANDS
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.routing import build_router


async def keep(
    sessions,
    chat_id: int,
    message_id: int,
    text: str,
    kind: MessageKind,
    at: datetime,
    reads_as: tuple[dict, ...] | None = None,
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
            reads_as=reads_as,
        )
    )


def route_call(call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "route", "arguments": '{"name": "workspace_mutator"}'},
            }
        ],
    }


def route_result(call_id: str, *did: str) -> dict:
    receipt = {"subagent": "workspace_mutator", "outcome": "done", "did": list(did)}
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": "route",
        "content": json.dumps(receipt, ensure_ascii=False),
    }


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
    assert dialogue[-1].content == "Current turn"


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
    assert dialogue[-1].content.endswith("Как дела?")
    assert dialogue[-1].content.count("Как дела?") == 1


async def test_the_owner_s_side_is_one_turn_and_only_it_carries_the_time(sessions) -> None:
    """TG-SHAPE-010 — tests/brd/tg_agent_shell/telegram_history.feature"""
    source = history_source(sessions)
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    entries = [
        HistoryEntry(1, "user", "First thought", at, "dialogue_user"),
        HistoryEntry(2, "user", "Second thought", at, "dialogue_user"),
        HistoryEntry(3, "assistant", "Safwa reply", at + timedelta(minutes=65), "dialogue_assistant"),
        HistoryEntry(4, "user", "Follow-up", at + timedelta(minutes=70), "dialogue_user"),
    ]

    async def recent(*_args, **_kwargs):
        return entries

    source.recent = recent  # type: ignore[method-assign]
    dialogue = await source.dialogue(100)

    # The owner's words carry no label, and an answer never carries the time: the hour it
    # opened is stamped on the owner's next words instead.
    assert [(item.role, item.content) for item in dialogue] == [
        ("user", "[2026-08-08 12:00] First thought\nSecond thought"),
        ("assistant", "Safwa reply"),
        ("user", "[2026-08-08 13:10] Follow-up"),
    ]


async def test_a_line_the_interface_wrote_reads_as_a_system_event(sessions) -> None:
    """TG-SYSTEM-016 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 119
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    await keep(sessions, chat_id, 1, "Rename the Tag", MessageKind.DIALOGUE_USER, at)
    await keep(
        sessions, chat_id, 2, "<b>Request interrupted</b>\nNothing was saved.",
        MessageKind.EVENT, at + timedelta(minutes=1),
    )
    await keep(
        sessions, chat_id, 3, "✅ Created <b>Stretch</b>.", MessageKind.EVENT,
        at + timedelta(minutes=2),
    )
    await keep(sessions, chat_id, 4, "Call it Home", MessageKind.DIALOGUE_USER, at + timedelta(minutes=3))

    dialogue = await history_source(sessions).dialogue(chat_id)

    assert [(item.role, item.content) for item in dialogue] == [
        (
            "user",
            "[2026-08-08 12:00] Rename the Tag\n"
            "[System]: Request interrupted\nNothing was saved.\n"
            "[System]: ✅ Created Stretch.\n"
            "Call it Home",
        ),
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
    host = ChatHost(TelegramNotes(sessions), spawn=spawn_timer)
    await host.keep(
        owner_message(80, "Before the Summary", chat_id), kind=MessageKind.DIALOGUE_USER.value
    )
    body = "An earlier part of the conversation. " * 150
    anchor = FakeMessage(81, bot_message=True, chat_id=chat_id, answer_as_new=True)
    last = await host.send_parts(
        anchor, html.escape(f"{SUMMARY_HEADER}\n{body}"), kind=MessageKind.SUMMARY.value,
        replace=False,
    )
    await host.keep(
        owner_message(2_000, "After the Summary", chat_id), kind=MessageKind.DIALOGUE_USER.value
    )

    entries = await history_source(sessions).recent(chat_id)

    assert len(anchor.sent_messages) > 1
    assert entries[0].kind == MessageKind.SUMMARY.value
    assert entries[0].text == f"[Summary]: {body.strip()}"
    # Kept once, on its first part; the others keep no words of their own.
    assert entries[0].message_id == anchor.sent_messages[0].message_id
    assert (await TelegramNotes(sessions).note(chat_id, last.message_id)).text is None
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


async def test_an_answer_reads_back_as_its_calls_their_results_and_its_words(sessions) -> None:
    """TG-TOOLS-013 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 120
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    await keep(sessions, chat_id, 1, "Add a card to buy milk", MessageKind.DIALOGUE_USER, at)
    turn = (
        route_call("call-1"),
        route_result("call-1", "✅ Saved — New Action “Buy milk” (1 EP)"),
        {"role": "assistant", "content": "Done — [Buy milk](card:1) is in your Backlog."},
    )
    await keep(
        sessions, chat_id, 2, "✅ Saved — New Action “Buy milk” (1 EP)\n\nDone — Buy milk.",
        MessageKind.DIALOGUE_ASSISTANT, at + timedelta(minutes=2), reads_as=turn,
    )
    await keep(sessions, chat_id, 3, "And eggs too", MessageKind.DIALOGUE_USER, at + timedelta(minutes=5))

    dialogue = await history_source(sessions).dialogue(chat_id)

    assert [item.as_message() for item in dialogue] == [
        {"role": "user", "content": "[2026-08-08 12:00] Add a card to buy milk"},
        *turn,
        {"role": "user", "content": "And eggs too"},
    ]


async def test_a_read_comes_back_as_its_call_and_a_receipt_comes_back_whole(sessions) -> None:
    """TG-READS-014 — tests/brd/tg_agent_shell/telegram_history.feature"""
    read = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "read-1",
                "type": "function",
                "function": {"name": "query_data", "arguments": '{"sql": "SELECT id FROM ai_cards"}'},
            }
        ],
    }
    rows = {"role": "tool", "tool_call_id": "read-1", "name": "query_data", "content": "[{\"id\": 1}]"}
    receipt = route_result("call-1", "✅ Saved — Edit Action “Buy milk”")

    turn = kept_turn([read, rows, route_call("call-1"), receipt], "Renamed it.")

    assert turn == (
        read,
        {**rows, "content": json.dumps(CLEARED_READ)},
        route_call("call-1"),
        receipt,
        {"role": "assistant", "content": "Renamed it."},
    )
    chat_id = 121
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    await keep(sessions, chat_id, 1, "Rename it", MessageKind.DIALOGUE_USER, at)
    await keep(
        sessions, chat_id, 2, "Renamed it.", MessageKind.DIALOGUE_ASSISTANT, at, reads_as=turn
    )
    dialogue = await history_source(sessions).dialogue(chat_id)
    assert [item.as_message() for item in dialogue][1:] == list(turn)


async def test_a_cue_reads_back_after_the_request_that_caused_it(sessions) -> None:
    """TG-CUE-015 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 122
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    await keep(sessions, chat_id, 1, "What is left today?", MessageKind.DIALOGUE_USER, at)
    await keep(
        sessions, chat_id, 2, "Two Actions.", MessageKind.DIALOGUE_ASSISTANT, at,
        reads_as=({"role": "assistant", "content": "Two Actions."},),
    )
    await keep(
        sessions, chat_id, 3, "Time to stretch.", MessageKind.CUE, at + timedelta(minutes=30),
        reads_as=kept_turn([], "Time to stretch.", request="Reminder: stretch at noon."),
    )

    dialogue = await history_source(sessions).dialogue(chat_id)

    # Two answers never merge: the Cue follows the request it answered.
    assert [(item.role, item.content) for item in dialogue] == [
        ("user", "[2026-08-08 12:00] What is left today?"),
        ("assistant", "Two Actions."),
        ("user", "Reminder: stretch at noon."),
        ("assistant", "Time to stretch."),
    ]


async def test_a_relayed_transcript_reads_as_the_owner_s_words_alone(sessions) -> None:
    """TG-RELAY-004 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 123
    host = ChatHost(TelegramNotes(sessions), spawn=spawn_timer)

    sent = await host.relay(
        next_in_chat(5, chat_id), "User Name", "Plan my week.", kind=MessageKind.DIALOGUE_USER.value
    )

    assert sent.answers[-1].startswith("<b>User Name:</b>")
    dialogue = await history_source(sessions).dialogue(chat_id)
    assert [(item.role, item.content.split("] ", 1)[1]) for item in dialogue] == [
        ("user", "Plan my week.")
    ]


async def test_an_answer_is_read_whole_with_its_calls_or_not_at_all(sessions) -> None:
    """TG-WINDOW-005 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 124
    at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    await keep(sessions, chat_id, 1, "Older words", MessageKind.DIALOGUE_USER, at)
    heavy = kept_turn(
        [route_call("call-1"), route_result("call-1", *(["✅ Saved — a line"] * 40))], "Done."
    )
    await keep(
        sessions, chat_id, 2, "Done.", MessageKind.DIALOGUE_ASSISTANT, at, reads_as=heavy
    )
    await keep(sessions, chat_id, 3, "Newest words", MessageKind.DIALOGUE_USER, at)

    # Its words are short, but what it put in front of the model is not.
    entries = await history_source(sessions).recent(chat_id, token_budget=150)

    assert [entry.text for entry in entries] == ["Newest words"]
    entries = await history_source(sessions).recent(chat_id, token_budget=1_000)
    assert [(entry.text, len(entry.turn)) for entry in entries] == [
        ("Older words", 0),
        ("Done.", 3),
        ("Newest words", 0),
    ]


async def test_an_answer_reads_back_with_the_citations_the_model_wrote(sessions) -> None:
    """TG-CITE-011 — tests/brd/tg_agent_shell/telegram_history.feature"""
    chat_id = 125
    words = "Answer **[Milk](check:14)** first."
    await keep(
        sessions, chat_id, 1,
        'Answer <b><a href="https://t.me/safwa_ai_bot?start=check-14">Milk</a></b> first.',
        MessageKind.DIALOGUE_ASSISTANT, datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        reads_as=({"role": "assistant", "content": words},),
    )

    dialogue = await history_source(sessions).dialogue(chat_id)

    assert [(item.role, item.content) for item in dialogue] == [("assistant", words)]


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
            {
                "role": "user",
                "content": (
                    "[2026-08-08 12:00] Создай действие убраться в комнате\n"
                    "[System]: Request interrupted."
                ),
            },
            route_call("call-1"),
            route_result("call-1", "✅ Saved — New Action “Убраться” (Backlog · 2 EP)"),
            {"role": "tool", "tool_call_id": "r", "name": "query_data", "content": "[]"},
            {"role": "assistant", "content": "Готово!\nЧто дальше?"},
        ]
    )

    # What `route` did is kept; a read is how the Advisor answered, not what it said.
    assert block == (
        "<Conversation>\n"
        '<User at="2026-08-08 12:00">Создай действие убраться в комнате</User>\n'
        "<System>Request interrupted.</System>\n"
        "<ToolResult>✅ Saved — New Action “Убраться” (Backlog · 2 EP)</ToolResult>\n"
        "<Advisor>Готово!\nЧто дальше?</Advisor>\n"
        "</Conversation>"
    )


def test_conversation_block_keeps_the_newest_things_said() -> None:
    dialogue = [{"role": "user", "content": f"message {index}"} for index in range(3)]
    dialogue.insert(1, {"role": "assistant", "content": "an answer"})

    assert conversation_block(dialogue, last=2) == (
        "<Conversation>\n<Advisor>an answer</Advisor>\n<User>message 1\nmessage 2</User>\n"
        "</Conversation>"
    )


def test_conversation_block_is_empty_when_the_conversation_is() -> None:
    assert conversation_block([]) == ""
