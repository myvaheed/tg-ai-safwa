"""Owner commands pass through the real dispatcher and access middleware."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    DeleteMessage,
    DeleteMessages,
    EditMessageText,
    GetFile,
    GetMe,
    SendMessage,
)
from aiogram.types import CallbackQuery, Chat, File, Message, Update, User, Voice
from test_access import protected_services, set_word
from ui_harness import ScriptedTranscriber

from safwa.features.cards.use_cases import create_card
from tg_agent_shell.access import manager
from tg_agent_shell.access.credentials import SECRET_LIKE_DISTANCE, SECRET_LIKE_LENGTH
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import register_message
from tg_agent_shell.telegram import owner_anchor
from tg_agent_shell.telegram.routing import build_router

pytestmark = pytest.mark.e2e


class Telegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.sent = []
        self.next_id = 1000

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name="Safwa", username="safwa_bot")
        if isinstance(method, (SendMessage, EditMessageText)):
            self.next_id += 1
            message = Message(
                message_id=method.message_id if isinstance(method, EditMessageText) else self.next_id,
                date=utcnow(), chat=Chat(id=int(method.chat_id), type="private"),
                from_user=User(id=bot.id, is_bot=True, first_name="Safwa"),
                text=method.text, reply_markup=method.reply_markup,
            ).as_(bot)
            self.sent.append(message)
            return message
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id=method.file_id, file_path="voice.ogg")
        return True

    def deleted(self):
        return {
            message_id
            for method in self.calls if isinstance(method, DeleteMessages)
            for message_id in method.message_ids
        } | {method.message_id for method in self.calls if isinstance(method, DeleteMessage)}

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        if False:
            yield b""


def message(bot, message_id, text):
    return Message(
        message_id=message_id, date=utcnow(), chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Owner"), text=text,
    ).as_(bot)


def voice(bot, message_id):
    return Message(
        message_id=message_id, date=utcnow(), chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Owner"),
        voice=Voice(file_id="voice", file_unique_id="voice", duration=3),
    ).as_(bot)


def dispatcher(services):
    result = Dispatcher()
    result.include_router(build_router(services.commands))
    result["services"] = services
    return result


@pytest.mark.parametrize("protected", [False, True])
async def test_lock_clears_everything_and_requires_a_configured_secret_word(sessions, protected):
    """HM-LOCK-016 — tests/brd/home.feature"""
    if protected:
        await set_word(sessions)
    services = protected_services(sessions)
    taken_up = []

    def spawn(work, name):
        taken_up.append(asyncio.create_task(work, name=name))
        return taken_up[-1]

    services.chat = replace(services.chat, spawn=spawn)
    async with sessions() as session:
        for message_id, kind, text in (
            (1, MessageKind.DIALOGUE_USER, "Private words"),
            (2, MessageKind.CUE, "Unanswered hook"),
            (3, MessageKind.CUE, "Unanswered Reminder"),
        ):
            await register_message(
                session, 42, message_id, "in" if message_id == 1 else "out", kind, text=text,
            )
        await session.commit()
    telegram = Telegram()
    async with Bot("999999:TEST_TOKEN", session=telegram) as bot:
        updates = dispatcher(services)
        await updates.feed_update(bot, Update(update_id=1, message=message(bot, 4, "/lock")))
        assert services.access.blocked is protected
        if not protected:
            assert not {1, 2, 3} & telegram.deleted()
            assert "Set Secret word" in telegram.sent[-1].text
            assert len(await services.history.dialogue(42)) == 3
            return
        assert {1, 2, 3, 4} <= telegram.deleted()
        assert telegram.sent == [] and await services.history.dialogue(42) == []
        await updates.feed_update(bot, Update(update_id=2, message=message(bot, 5, "/start")))
        assert services.access.blocked and "Secret word" in telegram.sent[-1].text
        await updates.feed_update(bot, Update(update_id=3, message=message(bot, 6, "x")))
        await asyncio.wait_for(asyncio.gather(*taken_up), 5)
        assert not services.access.blocked
        assert sum("🏠" in sent.text for sent in telegram.sent) == 1


@pytest.mark.parametrize("protected", [False, True])
async def test_start_menu_and_card_commands_work_in_an_open_session(sessions, protected):
    """HM-START-011 — tests/brd/home.feature"""
    if protected:
        await set_word(sessions)
    services = protected_services(sessions)
    telegram = Telegram()
    async with Bot("999999:TEST_TOKEN", session=telegram) as bot:
        updates = dispatcher(services)
        if protected:
            await services.access.lock(owner_anchor(bot, 42))
            await updates.feed_update(bot, Update(update_id=1, message=message(bot, 1, "x")))
            assert not services.access.blocked
        await updates.feed_update(bot, Update(update_id=2, message=message(bot, 2, "/start")))
        home = telegram.sent[-1]
        assert "🏠" in home.text
        assert [button.text for row in home.reply_markup.inline_keyboard for button in row] == ["☰ Menu"]
        await updates.feed_update(bot, Update(update_id=3, callback_query=CallbackQuery(
            id="menu", from_user=message(bot, 3, "").from_user, chat_instance="test",
            message=home, data="nav:home",
        )))
        assert any(button.text == "⚙️ Profile" for row in telegram.sent[-1].reply_markup.inline_keyboard for button in row)
        async with sessions() as session:
            card = await create_card(session, kind="action", title="Visible Card")
            await session.commit()
            card_id = card.id
        await updates.feed_update(bot, Update(update_id=4, message=message(bot, 4, f"/start card-{card_id}")))
        assert "Visible Card" in telegram.sent[-1].text and telegram.sent[-1].reply_markup is not None


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("command, expected", [("/start", "🏠"), ("/tags", "Tags"), ("/start card-1", "Visible Card")])
async def test_commands_waiting_during_unlock_run_only_if_the_secret_was_already_verified(
    sessions, monkeypatch, authenticated, command, expected,
):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    async with sessions() as session:
        await create_card(session, kind="action", title="Visible Card")
        await session.commit()
    services = protected_services(sessions)
    telegram = Telegram()
    async with Bot("999999:TEST_TOKEN", session=telegram) as bot:
        updates = dispatcher(services)
        await services.access.lock(owner_anchor(bot, 42))
        reached, resume, command_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        held_method = "_clear" if authenticated else "_locked_input"
        original = getattr(services.access, held_method)
        intercept = services.access.intercept

        async def pause(*args, **options):
            reached.set()
            await resume.wait()
            return await original(*args, **options)

        async def observed_intercept(event, again=None):
            if getattr(event, "text", None) == command:
                command_waiting.set()
            return await intercept(event, again)

        monkeypatch.setattr(services.access, held_method, pause)
        monkeypatch.setattr(services.access, "intercept", observed_intercept)
        unlock = asyncio.create_task(updates.feed_update(bot, Update(update_id=1, message=message(bot, 1, "x"))))
        await asyncio.wait_for(reached.wait(), 5)
        assert services.access.unlocking is authenticated
        start = asyncio.create_task(updates.feed_update(bot, Update(update_id=2, message=message(bot, 2, command))))
        await asyncio.wait_for(command_waiting.wait(), 5)
        assert telegram.sent == []
        resume.set()
        await asyncio.wait_for(asyncio.gather(unlock, start), 5)
        assert not services.access.blocked
        if authenticated:
            assert telegram.sent and expected in telegram.sent[-1].text
            assert telegram.sent[-1].reply_markup is not None
        else:
            assert telegram.sent == []


@pytest.mark.parametrize("words", ["text", "recording", None])
async def test_the_last_words_sent_while_locked_are_answered_once_the_word_matches(
    e2e_harness, monkeypatch, words,
):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    sessions = e2e_harness.sessions
    word = "open sesame"
    await set_word(sessions, word)
    services = protected_services(sessions)
    services.root, provider = e2e_harness.advisor(["Here is your answer."], plan_required=False)
    services.transcriber = ScriptedTranscriber("What is due today?")
    taken_up: list[asyncio.Task] = []

    def spawn(work, name):
        taken_up.append(asyncio.create_task(work, name=name))
        return taken_up[-1]

    services.chat = replace(services.chat, spawn=spawn)
    resumed = []

    async def resume_cues(*args):
        resumed.append(args)

    monkeypatch.setattr(manager, "resume_cues", resume_cues)
    telegram = Telegram()
    async with Bot("999999:TEST_TOKEN", session=telegram) as bot:
        updates = dispatcher(services)
        await services.access.lock(owner_anchor(bot, 42))
        said = {
            "text": [message(bot, 1, "First try"), message(bot, 2, "What is due today?")],
            "recording": [message(bot, 1, "First try"), voice(bot, 2)],
            None: [],
        }[words]
        tries = [
            message(bot, 3, "q" * SECRET_LIKE_LENGTH),
            message(bot, 4, "X" * SECRET_LIKE_DISTANCE + word[SECRET_LIKE_DISTANCE:]),
            message(bot, 5, word),
        ]
        for update_id, owner_message in enumerate([*said, *tries], 1):
            await updates.feed_update(bot, Update(update_id=update_id, message=owner_message))
        await asyncio.wait_for(asyncio.gather(*taken_up), 5)
    prompts = {item.message_id for item in telegram.sent if item.text == manager.ENTER_SECRET_WORD}
    assert len(prompts) == len(said) + 2 and prompts | {3, 4, 5} <= telegram.deleted()
    assert not any("🏠" in (item.text or "") for item in telegram.sent)
    dialogue = await services.history.dialogue(42)
    if words is None:
        assert len(resumed) == 1 and provider.calls == [] and dialogue == []
        return
    assert 1 in telegram.deleted() and 2 not in telegram.deleted() and resumed == []
    [call] = provider.calls
    assert "What is due today?" in str(call[-1]["content"])
    assert telegram.sent[-1].text == "Here is your answer."
    assert ["What is due today?" in item.content for item in dialogue] == [True, False]
    assert dialogue[-1].content.endswith("Here is your answer.")


@pytest.mark.parametrize("inputs, word, expected", [
    (["text", "/tags"], "open sesame", "Tags"),
    (["/tags", "text"], "open sesame", "Here is your answer."),
    (["/start", "/tags"], "open sesame", "Tags"),
    (["/start card-1"], "open sesame", "Visible Card"),
    (["/tags"], "tags", "Tags"),
])
async def test_unlock_processes_only_the_last_message_including_commands(
    e2e_harness, monkeypatch, inputs, word, expected,
):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    sessions = e2e_harness.sessions
    await set_word(sessions, word)
    async with sessions() as session:
        await create_card(session, kind="action", title="Visible Card")
        await session.commit()
    services = protected_services(sessions)
    services.root, provider = e2e_harness.advisor(["Here is your answer."], plan_required=False)
    taken_up = []

    def spawn(work, name):
        taken_up.append(asyncio.create_task(work, name=name))
        return taken_up[-1]

    services.chat = replace(services.chat, spawn=spawn)
    resumed = []

    async def resume_cues(*args):
        resumed.append(args)

    monkeypatch.setattr(manager, "resume_cues", resume_cues)
    telegram = Telegram()
    async with Bot("999999:TEST_TOKEN", session=telegram) as bot:
        updates = dispatcher(services)
        await services.access.lock(owner_anchor(bot, 42))
        for message_id, text in enumerate(inputs, 1):
            text = "What is due today?" if text == "text" else text
            await updates.feed_update(bot, Update(
                update_id=message_id, message=message(bot, message_id, text),
            ))
        assert services.access.blocked and taken_up == [] and provider.calls == []
        assert all(item.text == manager.ENTER_SECRET_WORD for item in telegram.sent)
        for message_id, text in ((10, "q" * SECRET_LIKE_LENGTH), (11, word)):
            await updates.feed_update(bot, Update(
                update_id=message_id, message=message(bot, message_id, text),
            ))
        await asyncio.wait_for(asyncio.gather(*taken_up), 5)
    assert not services.access.blocked and resumed == []
    assert sum(expected in item.text for item in telegram.sent) == 1
    assert {10, 11} <= telegram.deleted()
    dialogue = await services.history.dialogue(42)
    if inputs[-1] == "text":
        assert len(provider.calls) == 1 and len(dialogue) == 2
        assert "What is due today?" in dialogue[0].content
    else:
        assert provider.calls == [] and dialogue == []
    assert not any("🏠" in item.text for item in telegram.sent)


@pytest.mark.parametrize("command, blocked", [("/lock", True), ("/clear", False)])
async def test_deferred_lock_and_clear_commands_run_once_after_unlock(sessions, command, blocked):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    await set_word(sessions)
    services = protected_services(sessions)
    taken_up = []

    def spawn(work, name):
        taken_up.append(asyncio.create_task(work, name=name))
        return taken_up[-1]

    services.chat = replace(services.chat, spawn=spawn)
    telegram = Telegram()
    async with Bot("999999:TEST_TOKEN", session=telegram) as bot:
        updates = dispatcher(services)
        await services.access.lock(owner_anchor(bot, 42))
        await updates.feed_update(bot, Update(update_id=1, message=message(bot, 1, command)))
        assert services.access.blocked and taken_up == []
        await updates.feed_update(bot, Update(update_id=2, message=message(bot, 2, "x")))
        await asyncio.wait_for(asyncio.gather(*taken_up), 5)
    assert services.access.blocked is blocked
    assert sum(
        isinstance(method, DeleteMessage) and method.message_id == 1 for method in telegram.calls
    ) == 1
    assert {1, 2} <= telegram.deleted() and await services.history.dialogue(42) == []
