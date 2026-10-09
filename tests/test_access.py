"""Access gates, exact words and replay use real storage and fake Telegram."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, SendMessage
from aiogram.types import BufferedInputFile, Chat, Message, User
from sqlalchemy import select
from telegram_fakes import QueueTestMessage
from ui_harness import FakeBot, FakeMessage, history_source, press, services_for

from safwa.features.home.telegram import command_clear, render_unlocked_home
from safwa.features.profile.api import secret_word_verifier
from safwa.features.profile.model import UserProfile
from safwa.features.profile.telegram import command_profile
from safwa.features.profile.use_cases import set_secret_word
from tg_agent_shell.access.credentials import hash_secret_word, matches_secret_word
from tg_agent_shell.access.manager import ENTER_SECRET_WORD, AccessManager
from tg_agent_shell.access.model import DeferredDelivery
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
from tg_agent_shell.telegram.dialogue import run_after_turn, run_before_turn
from tg_agent_shell.telegram.services import OwnerAndWritingMiddleware
from tg_agent_shell.telegram.text_input import handle_raw_text_input


def protected_services(sessions):
    services = services_for(sessions)
    services.history = history_source(sessions)
    services.owner_acted_at = utcnow() - timedelta(hours=1)
    services.access = AccessManager(services, secret_word_verifier, render_unlocked_home)
    services.history.access_boundaries = True
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


@pytest.mark.parametrize("word", ["x", "🔑", "/clear", " a\n ", " "])
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
    answer = FakeMessage(901, text=word, bot_message=False, bot=message.bot)
    assert await handle_raw_text_input(answer, services)
    assert answer.was_deleted
    assert not services.access.blocked
    assert not any(word == note.text for note in await TelegramNotes(sessions).outgoing(700))
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        assert matches_secret_word(word, profile.secret_word_hash)
    assert "Secret word: set" in message.bot.edits[-1][1]
    assert await services.history.dialogue(700) == []


async def test_off_disables_the_secret_without_an_extra_button(sessions):
    """PS-SECRET-023 — tests/brd/profile.feature"""
    await set_word(sessions)
    await set_word(sessions, "OFF")
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).secret_word_hash is None
    await set_word(sessions, " off ")
    async with sessions() as session:
        assert matches_secret_word(" off ", (await session.get(UserProfile, 1)).secret_word_hash)


async def test_lock_clears_hooks_then_unlock_restores_only_unanswered_hooks_before_home(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions, "/clear")
    services = protected_services(sessions)
    anchor = QueueTestMessage(message_id=10, answer_as_new=True)
    await keep(sessions, 1, "Answered hook")
    await keep(sessions, 2, "My answer", MessageKind.DIALOGUE_USER)
    await keep(sessions, 3, "Unanswered hook")
    await services.access.lock(anchor)
    assert anchor.bot.deleted == [1, 2, 3]
    assert anchor.bot.drawn == []
    assert await services.history.dialogue(700) == []
    await services.access.lock(anchor)
    wrong = incoming(anchor, "no", 100)
    await services.access.intercept(wrong)
    assert services.access.blocked
    assert wrong.rendered == [ENTER_SECRET_WORD]
    assert await services.access.defer(
        anchor, {"text": "New hook", "kind": "cue"}, event_id="a" * 32
    )
    correct = incoming(anchor, "/clear", 2000)
    await services.access.intercept(correct)
    assert not services.access.blocked
    assert correct.rendered[-3:-1] == ["Unanswered hook", "New hook"]
    assert "🏠" in correct.rendered[-1]
    assert 100 in anchor.bot.deleted and 2000 in anchor.bot.deleted
    assert wrong.sent[0].message_id in anchor.bot.deleted
    dialogue = await services.history.dialogue(700)
    assert [item.content for item in dialogue] == ["Unanswered hook", "New hook"]
    async with sessions() as session:
        assert list(await session.scalars(select(DeferredDelivery))) == []
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
    assert transcript.count("Unanswered hook") == 1
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


async def test_clear_still_draws_home_and_automatic_home_locks_without_drawing(sessions):
    """HM-LOCK-015 — tests/brd/home.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await keep(sessions, 1, "Old dialogue", MessageKind.DIALOGUE_USER)
    await command_clear(anchor, services)
    assert not services.access.blocked and "🏠" in anchor.rendered[-1]
    shown = len(anchor.rendered)
    await speak_on_schedule(services, anchor, "", "home", lambda: True)
    assert services.access.blocked and len(anchor.rendered) == shown


async def test_restart_keeps_prepared_deliveries_and_requires_word_again(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    await services.access.defer(anchor, {"text": "Prepared once", "kind": "cue"}, event_id="b" * 32)
    restarted = protected_services(sessions)
    await restarted.access.initialize(anchor)
    assert restarted.access.blocked and await restarted.access.accepted("b" * 32)
    await restarted.access.intercept(incoming(anchor, "x"))
    assert not restarted.access.blocked
    assert anchor.rendered.count("Prepared once") == 1
    again = protected_services(sessions)
    await again.access.initialize(anchor)
    assert again.access.blocked


async def test_failed_delivery_keeps_access_closed_and_retry_keeps_one_logical_turn(
    sessions, monkeypatch
):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await keep(sessions, 1, "First hook")
    await services.access.lock(anchor)
    await services.access.defer(anchor, {"text": "Second hook", "kind": "cue"}, event_id="c" * 32)
    original = services.access._deliver

    async def failing(message, row):
        if row.payload["text"] == "Second hook":
            raise TelegramBadRequest(method=None, message="Temporary failure")
        await original(message, row)

    monkeypatch.setattr(services.access, "_deliver", failing)
    await services.access.intercept(incoming(anchor, "x"))
    assert services.access.blocked
    assert await services.access.accepted("c" * 32)
    monkeypatch.setattr(services.access, "_deliver", original)
    await services.access.intercept(incoming(anchor, "x", 4000))
    assert not services.access.blocked
    async with sessions() as session:
        texts = list(
            await session.scalars(select(TelegramMessage.text).where(TelegramMessage.kind == "cue"))
        )
    assert texts.count("First hook") == texts.count("Second hook") == 1


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("kind", ["cue", "event"])
async def test_partial_long_delivery_recovers_without_a_second_logical_copy(
    sessions, monkeypatch, restart, kind
):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    body = "A long hook line.\n" * 400
    await services.access.defer(anchor, {"text": body, "kind": kind}, event_id="d" * 32)
    first = incoming(anchor, "x")
    answer = first.answer
    parts = 0

    async def partial(text, **options):
        nonlocal parts
        parts += 1
        if parts == 2:
            raise TelegramBadRequest(method=None, message="Second part did not reach Telegram")
        return await answer(text, **options)

    monkeypatch.setattr(first, "answer", partial)
    await services.access.intercept(first)
    assert services.access.blocked and parts == 2
    if restart:
        services = protected_services(sessions)
        await services.access.initialize(anchor)
    await services.access.intercept(incoming(anchor, "x", 6000))
    assert not services.access.blocked
    async with sessions() as session:
        copies = list(
            await session.scalars(select(TelegramMessage).where(TelegramMessage.text == body))
        )
        assert len(copies) == 1
        assert list(await session.scalars(select(DeferredDelivery))) == []


async def test_unlock_waits_for_running_background_work_and_publishes_its_result(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    assert services.turn.try_begin_background()
    revision = services.turn.dialogue_revision
    unlocking = asyncio.create_task(services.access.intercept(incoming(anchor, "x")))
    for _ in range(100):
        if services.access.unlocking:
            break
        await asyncio.sleep(0.005)
    assert services.access.unlocking and anchor.rendered == []
    await services.access.defer(anchor, {"text": "Finished during entry", "kind": "cue"})
    services.turn.end_background(revision)
    await asyncio.wait_for(unlocking, 5)
    assert anchor.rendered[0] == "Finished during entry"
    assert "🏠" in anchor.rendered[-1]


async def test_images_are_kept_without_telegram_and_sent_before_home(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    await services.access.photos(
        anchor, [BufferedInputFile(b"private-png", filename="chart.png")], kind="receipt"
    )
    assert anchor.bot.photos_sent == [] and anchor.bot.typing_calls == 0
    await services.access.intercept(incoming(anchor, "x"))
    assert anchor.bot.photos_sent[0][0].data == b"private-png"
    assert "🏠" in anchor.bot.drawn[-1]


@pytest.mark.parametrize("when", ["before", "after", "after_error"])
async def test_run_hook_publications_and_errors_wait_for_unlock(sessions, when):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)

    async def evaluate(event):
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
    assert anchor.rendered == []
    await services.access.intercept(incoming(anchor, "x"))
    expected = "Private failure detail" if when == "after_error" else "Private hook words"
    assert expected in anchor.rendered[-2] and "🏠" in anchor.rendered[-1]


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


async def test_restored_passing_hook_starts_its_lifetime_when_shown(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    timers = []

    def spawn(work, name):
        task = asyncio.create_task(work, name=name)
        timers.append(task)
        return task

    services.chat = replace(services.chat, spawn=spawn)
    try:
        await keep(sessions, 1, "Temporary hook", MessageKind.PASSING_CUE)
        await services.chat.let_pass(anchor, kind="passing_cue", seconds=300)
        await services.access.lock(anchor)
        async with sessions() as session:
            pending = await session.scalar(select(DeferredDelivery))
            assert pending.payload["passing_seconds"] == 300
        await services.access.intercept(incoming(anchor, "x"))
        notes = await TelegramNotes(sessions).outgoing(700, kinds={"passing_cue"})
        assert len(notes) == 1 and notes[0].passing_seconds == 300
        assert notes[0].message_id > 2000 and len(timers) == 2
    finally:
        for task in timers:
            task.cancel()
        await asyncio.gather(*timers, return_exceptions=True)


async def test_day_read_keeps_today_when_an_older_hook_is_replayed_below_it(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    now = utcnow()
    async with sessions() as session:
        await register_message(
            session,
            700,
            1,
            "out",
            MessageKind.CUE,
            text="Yesterday hook",
            at=now - timedelta(days=1),
        )
        await register_message(
            session, 700, 2, "out", MessageKind.DIALOGUE_ASSISTANT, text="Today answer", at=now
        )
        await session.commit()
    await services.access.lock(anchor)
    await services.access.intercept(incoming(anchor, "x"))
    today = await services.history.day_transcript(
        700, start=now - timedelta(hours=1), end=now + timedelta(hours=1), token_budget=10000
    )
    assert "Today answer" in today and "Yesterday hook" not in today
    yesterday = await services.history.day_transcript(
        700, start=now - timedelta(days=2), end=now - timedelta(hours=1), token_budget=10000
    )
    assert yesterday.count("Yesterday hook") == 1


async def test_replayed_old_hook_uses_its_new_display_time_for_cleanup(sessions):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    now = utcnow()
    original = now - timedelta(days=3)
    async with sessions() as session:
        await register_message(
            session, 700, 1, "out", MessageKind.CUE, text="Old hook", at=original
        )
        await session.commit()
    await services.access.lock(anchor)
    await services.access.intercept(incoming(anchor, "x"))
    async with sessions() as session:
        hook = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.text == "Old hook")
        )
        assert hook.created_at == original and hook.displayed_at >= now
        shown_id = hook.message_id
        # The word is already outside 48 hours; the replay and Home are still deletable.
        hook.displayed_at = now - timedelta(hours=47, minutes=55)
        word = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.kind == MessageKind.UI_INPUT.value)
        )
        word.created_at = now - timedelta(hours=48, minutes=5)
        home = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.kind == MessageKind.DASHBOARD.value)
        )
        home.created_at = home.displayed_at = now - timedelta(hours=47, minutes=50)
        await session.commit()
    anchor.bot.deleted.clear()
    restarted = protected_services(sessions)
    await restarted.access.initialize(anchor)
    assert shown_id in anchor.bot.deleted


async def test_real_telegram_middleware_accepts_a_command_as_setting_and_unlock_word(sessions):
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
    middleware = OwnerAndWritingMiddleware(raw_input=handle_raw_text_input)
    await middleware(handler, event, {"services": services})
    handler.assert_not_called()
    assert 1000 in bot.deleted and not services.access.blocked
    async with sessions() as session:
        assert matches_secret_word("/clear", (await session.get(UserProfile, 1)).secret_word_hash)
    await services.access.lock(screen)
    event = event.model_copy(update={"message_id": 4000}).as_(bot)
    await middleware(handler, event, {"services": services})
    assert not services.access.blocked and "🏠" in bot.sent[-1]
    handler.assert_not_called()
