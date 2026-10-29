"""Access gates and exact words use real storage and fake Telegram."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, SendMessage
from aiogram.types import Chat, Message, User
from sqlalchemy import select
from telegram_fakes import QueueTestMessage
from ui_harness import FakeBot, FakeMessage, history_source, press, services_for

from safwa.features.home.telegram import command_clear, command_lock, render_home
from safwa.features.profile.api import secret_word_verifier
from safwa.features.profile.model import UserProfile
from safwa.features.profile.telegram import command_profile
from safwa.features.profile.use_cases import set_secret_word
from tg_agent_shell.access import manager
from tg_agent_shell.access.credentials import (
    hash_secret_word,
    matches_secret_word,
    resembles_secret_word,
)
from tg_agent_shell.access.manager import ENTER_SECRET_WORD, AccessManager
from tg_agent_shell.cues.runtime import speak_on_schedule
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage, TelegramNotes, register_message
from tg_agent_shell.hooks.contracts import (
    AfterTurn,
    BeforeTurn,
    HookSpec,
    OnAfterTurn,
    OnBeforeTurn,
    Run,
)
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.telegram.dialogue import ordinary_text, run_after_turn, run_before_turn
from tg_agent_shell.telegram.services import OwnerAndWritingMiddleware


def protected_services(sessions):
    services = services_for(sessions)
    services.history = history_source(sessions)
    services.owner_acted_at = utcnow() - timedelta(hours=1)
    services.access = AccessManager(services, secret_word_verifier)
    return services


async def set_word(sessions, raw="x"):
    async with sessions() as session:
        await set_secret_word(session, raw)
        await session.commit()


async def keep(sessions, message_id, text, kind=MessageKind.CUE):
    async with sessions() as session:
        await register_message(
            session,
            700,
            message_id,
            "in" if kind == MessageKind.DIALOGUE_USER else "out",
            kind,
            text=text,
        )
        await session.commit()


def incoming(parent, text, message_id=2000):
    return QueueTestMessage(
        message_id=message_id, text=text, is_bot=False, answer_as_new=True, parent=parent
    )


async def notification(services, anchor, text, *, event_id=None):
    await services.chat.send_parts(anchor, text, kind="cue", event_id=event_id, replace=False)


async def typed_into_profile(services, message, *words):
    entries = [
        FakeMessage(901 + index, text=word, bot_message=False, bot=message.bot)
        for index, word in enumerate(words)
    ]
    for entry in entries:
        await ordinary_text(entry, services)
    return entries


async def stored_verifier(sessions):
    async with sessions() as session:
        return (await session.get(UserProfile, 1)).secret_word_hash


@pytest.mark.parametrize("word", ["x", "🔑", " a\n ", " ", "off", "OFF"])
async def test_secret_word_is_exact_salted_text_and_profile_input_is_not_dialogue(sessions, word):
    """PS-SECRET-023 — tests/brd/profile.feature"""
    first, second = hash_secret_word(word), hash_secret_word(word)
    assert first != second
    assert matches_secret_word(word, first)
    assert not matches_secret_word(word + " ", first)
    services = protected_services(sessions)
    message = FakeMessage(900, bot_message=True, answer_as_new=True)
    await command_profile(message, services)
    await press(services, message, message.edits[-1][1], "🔐 Secret word")
    [once] = await typed_into_profile(services, message, word)
    assert await stored_verifier(sessions) is None
    assert "Send the same Secret word again" in message.bot.edits[-1][1]
    [twice] = await typed_into_profile(services, message, word)
    assert once.was_deleted and twice.was_deleted
    assert not services.access.blocked
    assert not any(word == note.text for note in await TelegramNotes(sessions).outgoing(700))
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        assert matches_secret_word(word, profile.secret_word_hash)
    assert "<i>Secret word:</i> set" in message.bot.edits[-1][1]
    assert await services.history.dialogue(700) == []


async def test_secret_word_is_saved_only_when_sent_twice_the_same_and_never_as_a_command(
    sessions,
):
    """PS-SECRET-023 — tests/brd/profile.feature"""
    services = protected_services(sessions)
    message = FakeMessage(900, bot_message=True, answer_as_new=True)
    await command_profile(message, services)
    await press(services, message, message.edits[-1][1], "🔐 Secret word")
    await typed_into_profile(services, message, " /start")
    assert "A command cannot be the Secret word." in message.bot.edits[-1][1]
    await typed_into_profile(services, message, "first", "second")
    assert "The two entries differ" in message.bot.edits[-1][1]
    assert await stored_verifier(sessions) is None
    # A difference starts over: the next entry is a first one again.
    await typed_into_profile(services, message, "second")
    assert await stored_verifier(sessions) is None
    await typed_into_profile(services, message, "second")
    assert matches_secret_word("second", await stored_verifier(sessions))
    assert "Secret word updated." in message.bot.edits[-1][1]


async def test_remove_button_disables_the_secret_and_off_is_an_exact_word(sessions):
    """PS-SECRET-023 — tests/brd/profile.feature"""
    await set_word(sessions, "OFF")
    async with sessions() as session:
        assert matches_secret_word("OFF", (await session.get(UserProfile, 1)).secret_word_hash)
    services = protected_services(sessions)
    message = FakeMessage(900, bot_message=True, answer_as_new=True)
    await command_profile(message, services)
    await press(services, message, message.edits[-1][1], "🔐 Secret word")
    await press(services, message, message.bot.edits[-1][2], "Remove secret word")
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).secret_word_hash is None
    assert "<i>Secret word:</i> off" in message.bot.edits[-1][1]


async def test_lock_and_unlock_leave_unanswered_hooks_and_reminders_in_the_chat(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions, "Sesame")
    services = protected_services(sessions)
    anchor = QueueTestMessage(message_id=10, answer_as_new=True)
    await keep(sessions, 1, "Answered hook")
    await keep(sessions, 2, "My answer", MessageKind.DIALOGUE_USER)
    await keep(sessions, 3, "Unanswered hook")
    await services.access.lock(anchor)
    assert anchor.bot.deleted == [1, 2]
    assert anchor.bot.drawn == []
    assert [item.content for item in await services.history.dialogue(700)] == ["Unanswered hook"]
    await services.access.lock(anchor)
    wrong = incoming(anchor, "no", 100)
    await services.access.intercept(wrong)
    assert services.access.blocked
    assert wrong.rendered == [ENTER_SECRET_WORD]
    await notification(services, anchor, "New Reminder", event_id="a" * 32)
    reminder = anchor.sent[-1].message_id
    correct = incoming(anchor, "Sesame", 2000)
    await services.access.intercept(correct)
    assert not services.access.blocked
    assert correct.rendered[-2:] == [ENTER_SECRET_WORD, "New Reminder"]
    assert {100, 2000, wrong.sent[0].message_id} <= set(anchor.bot.deleted)
    assert 3 not in anchor.bot.deleted and reminder not in anchor.bot.deleted
    dialogue = await services.history.dialogue(700)
    assert [item.content for item in dialogue] == ["Unanswered hook", "New Reminder"]
    async with sessions() as session:
        assert (
            len(
                list(
                    await session.scalars(
                        select(TelegramMessage).where(TelegramMessage.text == "Unanswered hook")
                    )
                )
            )
            == 1
        )
    transcript = await services.history.day_transcript(
        700,
        start=utcnow() - timedelta(days=1),
        end=utcnow() + timedelta(days=1),
        token_budget=10000,
    )
    assert transcript.count("Unanswered hook") == 1 and transcript.count("New Reminder") == 1
    assert "My answer" in transcript and "Answered hook" in transcript


@pytest.mark.parametrize(
    "text", ["/start", "/clear", "/cancel", "/status", "/start card:1", "hello"]
)
async def test_every_locked_message_is_intercepted_before_handlers_and_cancellation(sessions, text):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    message = QueueTestMessage(answer_as_new=True)
    await services.access.lock(message)
    assert services.turn.try_begin_background()
    revision, acted = services.turn.dialogue_revision, services.owner_acted_at
    handler = AsyncMock()
    await OwnerAndWritingMiddleware()(handler, incoming(message, text), {"services": services})
    handler.assert_not_called()
    assert services.turn.dialogue_revision == revision and services.turn.background
    assert services.owner_acted_at == acted
    services.turn.end_background(revision)


async def test_locked_callbacks_edits_and_media_cannot_unlock_or_reach_handlers(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    callback = SimpleNamespace(
        from_user=anchor.from_user, message=anchor, answer=AsyncMock(), data="nav:home"
    )
    edit = incoming(anchor, "x")
    edit.edit_date = utcnow()
    photo = incoming(anchor, None, 2001)
    photo.photo = [SimpleNamespace(file_id="private")]
    voice = incoming(anchor, None, 2002)
    voice.voice = SimpleNamespace(duration=1)
    handler = AsyncMock()
    for event in (callback, edit, photo, voice):
        await OwnerAndWritingMiddleware()(handler, event, {"services": services})
    handler.assert_not_called()
    callback.answer.assert_awaited_once_with(ENTER_SECRET_WORD, show_alert=True)
    assert services.access.blocked and anchor.bot.downloads == []


async def test_locked_edit_does_not_overwrite_the_archived_owner_message(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await keep(sessions, 1, "Keep this for the Diary", MessageKind.DIALOGUE_USER)
    await services.access.lock(anchor)
    edit = incoming(anchor, "x", 1)
    edit.edit_date = utcnow()
    await services.access.intercept(edit)
    note = await TelegramNotes(sessions).note(700, 1)
    assert note.kind == "dialogue_user" and note.text == "Keep this for the Diary"
    assert services.access.blocked


async def test_clear_leaves_access_open_and_automatic_clear_locks_without_drawing(sessions):
    """HM-LOCK-015 — tests/brd/home.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await keep(sessions, 1, "Old dialogue", MessageKind.DIALOGUE_USER)
    await command_clear(anchor, services)
    assert not services.access.blocked and anchor.rendered == []
    shown = len(anchor.rendered)
    await speak_on_schedule(services, anchor, "", "home", lambda: True)
    assert services.access.blocked and len(anchor.rendered) == shown
    await services.access.intercept(incoming(anchor, "x"))
    assert not services.access.blocked and anchor.rendered == []
    await render_home(incoming(anchor, "/start", 3000), services)
    assert "🏠" in anchor.rendered[-1]
    assert [button.text for row in anchor.markups[-1].inline_keyboard for button in row] == ["☰ Menu"]


@pytest.mark.parametrize("manual", [False, True])
async def test_a_clear_telegram_refuses_keeps_access_locked_and_is_tried_again(
    sessions, monkeypatch, manual
):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    retries: list[asyncio.Task] = []

    def spawn(work, name):
        retries.append(asyncio.create_task(work, name=name))
        return retries[-1]

    services.chat = replace(services.chat, spawn=spawn)
    monkeypatch.setattr(manager, "CLEAR_RETRY_SECONDS", 0)
    anchor = QueueTestMessage(message_id=3, answer_as_new=True)
    await keep(sessions, 1, "Private words", MessageKind.DIALOGUE_USER)
    await keep(sessions, 2, "Unanswered hook")
    refusals = 2
    delete_messages = anchor.bot.delete_messages

    async def flaky(**options):
        nonlocal refusals
        if refusals:
            refusals -= 1
            raise TelegramBadRequest(method=None, message="Temporary failure")
        await delete_messages(**options)

    monkeypatch.setattr(anchor.bot, "delete_messages", flaky)
    # Startup locks too: a refusal there must not stop the bot.
    if manual:
        await command_lock(anchor, services)
    else:
        await services.access.initialize(anchor)
    assert services.access.blocked and anchor.bot.deleted == []
    await asyncio.wait_for(asyncio.gather(*retries), 5)
    assert services.access.blocked and refusals == 0
    assert anchor.bot.deleted == ([1, 2, 3] if manual else [1])


async def test_restart_requires_the_word_again_and_leaves_reminder_words(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    await notification(services, anchor, "Reminder arrived", event_id="b" * 32)
    reminder = anchor.sent[-1].message_id
    restarted = protected_services(sessions)
    await restarted.access.initialize(anchor)
    assert restarted.access.blocked
    await restarted.access.intercept(incoming(anchor, "x"))
    assert not restarted.access.blocked
    assert anchor.rendered.count("Reminder arrived") == 1 and reminder not in anchor.bot.deleted
    again = protected_services(sessions)
    await again.access.initialize(anchor)
    assert again.access.blocked


async def test_a_failed_unlock_keeps_access_closed_until_the_next_entry(sessions, monkeypatch):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    resume = manager.resume_cues
    failures = 1

    async def flaky(services, anchor):
        nonlocal failures
        if failures:
            failures -= 1
            raise TelegramBadRequest(method=None, message="Temporary failure")
        await resume(services, anchor)

    monkeypatch.setattr(manager, "resume_cues", flaky)
    await services.access.intercept(incoming(anchor, "x"))
    assert services.access.blocked
    await services.access.intercept(incoming(anchor, "x", 4000))
    assert not services.access.blocked and failures == 0


async def test_unlock_waits_for_a_running_reminder_and_leaves_its_words(sessions, monkeypatch):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    assert services.turn.try_begin_background()
    revision = services.turn.dialogue_revision
    waiting = asyncio.Event()
    wait_idle = services.turn.wait_idle

    async def wait_for_reminder():
        waiting.set()
        await wait_idle()

    monkeypatch.setattr(services.turn, "wait_idle", wait_for_reminder)
    unlocking = asyncio.create_task(services.access.intercept(incoming(anchor, "x")))
    try:
        await asyncio.wait_for(waiting.wait(), 5)
        assert not unlocking.done() and services.access.unlocking and anchor.rendered == []
        await notification(services, anchor, "Finished during entry")
        services.turn.end_background(revision)
        await asyncio.wait_for(unlocking, 5)
    finally:
        services.turn.end_background(revision)
        unlocking.cancel()
        await asyncio.gather(unlocking, return_exceptions=True)
    assert anchor.rendered == ["Finished during entry"]
    assert anchor.sent[0].message_id not in anchor.bot.deleted


@pytest.mark.parametrize("when", ["before", "after", "after_error"])
async def test_turn_hooks_are_not_evaluated_while_locked(sessions, when):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)

    evaluated = []

    async def evaluate(event):
        evaluated.append(event)
        return (True,)

    async def publish(payload, context):
        if when == "after_error":
            raise RuntimeError("Private failure detail")
        await context.publish("Private hook words", "event")

    on = OnBeforeTurn() if when == "before" else OnAfterTurn(source="system")
    services.hooks = HookRegistry.of(
        (
            HookSpec(
                name="test.publish",
                owner="test",
                on=(on,),
                evaluate=evaluate,
                effect=Run(publish),
                title="Test publication",
                description="Test publication",
            ),
        ),
        owners=frozenset({"test"}),
    )
    if when == "before":
        await run_before_turn(anchor, services, BeforeTurn(42, 700, 0, "system"), lambda: True)
    else:
        await run_after_turn(anchor, services, AfterTurn(42, 700, 1, 0, "system"))
    assert anchor.rendered == [] and evaluated == []
    await services.access.intercept(incoming(anchor, "x"))
    if when == "before":
        await run_before_turn(anchor, services, BeforeTurn(42, 700, 0, "system"), lambda: True)
    else:
        await run_after_turn(anchor, services, AfterTurn(42, 700, 1, 0, "system"))
    expected = "Private failure detail" if when == "after_error" else "Private hook words"
    assert expected in anchor.rendered[-1] and len(evaluated) == 1


def test_a_try_at_the_word_is_a_short_text_or_one_a_few_edits_from_it():
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    word = "open sesame"
    assert resembles_secret_word("short", word)
    assert not resembles_secret_word("longer", word)
    assert resembles_secret_word("XXXn sesame", word)
    assert not resembles_secret_word("XXXX sesame", word)
    assert resembles_secret_word("n sesame", word)
    assert not resembles_secret_word(" sesame", word)
    assert resembles_secret_word("open sesame!!!", word)
    assert not resembles_secret_word("open sesame!!!!", word)


@pytest.mark.parametrize("wrong_first", [False, True])
async def test_unlock_deletes_the_word_and_attempts_even_when_the_chat_was_empty(
    sessions, wrong_first
):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    if wrong_first:
        await services.access.intercept(incoming(anchor, "wrong", 2000))
    await services.access.intercept(incoming(anchor, "x", 4000))
    assert not services.access.blocked and 4000 in anchor.bot.deleted
    if wrong_first:
        assert 2000 in anchor.bot.deleted
    assert await services.history.dialogue(700) == []


async def test_a_command_sent_to_the_secret_word_editor_is_a_command(sessions):
    """PS-SECRET-023 — tests/brd/profile.feature"""

    class InputBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.sent = []

        async def __call__(self, method, request_timeout=None):
            if isinstance(method, DeleteMessage):
                self.deleted.append(method.message_id)
                return True
            if isinstance(method, SendMessage):
                self.sent.append(method.text)
                return FakeMessage(
                    5000 + len(self.sent), text=method.text, bot_message=True, bot=self
                )
            return await super().__call__(method, request_timeout)

    services = protected_services(sessions)
    bot = InputBot()
    screen = FakeMessage(900, bot_message=True, bot=bot, answer_as_new=True)
    await command_profile(screen, services)
    await press(services, screen, screen.edits[-1][1], "🔐 Secret word")
    event = Message(
        message_id=1000,
        date=utcnow(),
        chat=Chat(id=700, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Owner"),
        text="/clear",
    ).as_(bot)
    handler = AsyncMock()
    await OwnerAndWritingMiddleware()(handler, event, {"services": services})
    handler.assert_awaited_once()
    assert 1000 in bot.deleted and await stored_verifier(sessions) is None
