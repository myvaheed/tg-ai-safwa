"""Owner commands pass through the real dispatcher and access middleware."""

from __future__ import annotations

import asyncio

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from test_access import protected_services, set_word

from safwa.features.cards.use_cases import create_card
from tg_agent_shell.foundation.clock import utcnow
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
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        if False:
            yield b""


def message(bot, message_id, text):
    return Message(
        message_id=message_id, date=utcnow(), chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Owner"), text=text,
    ).as_(bot)


def dispatcher(services):
    result = Dispatcher()
    result.include_router(build_router(services.commands))
    result["services"] = services
    return result


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

        async def observed_intercept(event):
            if getattr(event, "text", None) == command:
                command_waiting.set()
            return await intercept(event)

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
