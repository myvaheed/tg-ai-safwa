"""The Telegram boundary, replaced. Nothing here knows which application is under test.

One copy, because both applications in this repository need it: Safwa's review tests reach
it through `review_e2e_helpers`, and `tests/shell/` builds the example bot on it without
importing Safwa at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from types import SimpleNamespace

from aiogram.types import InlineKeyboardMarkup


def spawn_timer(work: Coroutine[None, None, None], name: str) -> asyncio.Task[None]:
    """The Toast timer a test's host starts: no test shuts down, so no test cancels one."""
    return asyncio.create_task(work, name=name)


class QueueTestBot:
    def __init__(self) -> None:
        self.id = 999
        self.typing_calls = 0
        self.edits: list[str] = []
        # Everything drawn in this chat, in order, whether a message drew it or the bot
        # edited one in place. A screen replaced through the Bot API is still a screen.
        self.drawn: list[str] = []
        self.deleted: list[int] = []
        self.published_commands: list[list[str]] = []

    async def send_chat_action(self, _chat_id, _action) -> None:
        self.typing_calls += 1

    async def edit_message_text(
        self, text, *, chat_id, message_id, reply_markup=None, parse_mode=None
    ) -> None:
        del chat_id, message_id, reply_markup, parse_mode
        self.edits.append(text)
        self.drawn.append(text)

    async def delete_message(self, chat_id, message_id) -> None:
        del chat_id
        self.deleted.append(message_id)

    async def delete_messages(self, *, chat_id, message_ids) -> None:
        del chat_id
        self.deleted.extend(message_ids)

    async def edit_message_reply_markup(self, *, chat_id, message_id, reply_markup=None) -> None:
        del chat_id, message_id, reply_markup

    async def set_my_commands(self, commands) -> None:
        self.published_commands.append([command.command for command in commands])


class QueueTestMessage:
    """One message in the chat.

    `answer_as_new` is what a real chat does: an answer is a message of its own, with an
    id of its own. A test that only reads the last rendered text can leave it off and keep
    one message; a test that runs a whole turn cannot, because two screens sharing one id
    are one note, and taking the first one down would take the second one with it.
    """

    def __init__(
        self,
        *,
        message_id: int = 900,
        owner_id: int = 42,
        text: str = "",
        is_bot: bool = True,
        answer_as_new: bool = False,
        parent: QueueTestMessage | None = None,
    ) -> None:
        self.message_id = message_id
        self.chat = SimpleNamespace(id=700, type="private")
        self.from_user = SimpleNamespace(id=owner_id, is_bot=is_bot, full_name="Owner")
        self.bot = parent.bot if parent is not None else QueueTestBot()
        self.date = datetime.now(UTC)
        self.text = text
        self.owner_id = owner_id
        self.answer_as_new = answer_as_new
        # The whole chat, in order, wherever it was drawn: one list every message appends to.
        self.rendered: list[str] = parent.rendered if parent is not None else []
        self.markups: list[InlineKeyboardMarkup | None] = (
            parent.markups if parent is not None else []
        )
        self.sent: list[QueueTestMessage] = parent.sent if parent is not None else []
        self.was_deleted = False

    async def edit_text(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        self.bot.drawn.append(text)
        return self

    async def answer(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        self.bot.drawn.append(text)
        if not self.answer_as_new:
            return self
        # Telegram hands out a fresh id per message; a repeated one would collapse
        # several registrations into one row.
        sent = QueueTestMessage(
            message_id=self.message_id + 1_000 + len(self.sent),
            owner_id=self.owner_id,
            text=text,
            answer_as_new=True,
            parent=self,
        )
        self.sent.append(sent)
        return sent

    async def delete(self) -> None:
        self.was_deleted = True

    def buttons(self) -> list[str]:
        markup = self.markups[-1]
        return [button.text for row in markup.inline_keyboard for button in row]


class QueueTestCallback:
    def __init__(self, token: str, message: QueueTestMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text=None, *, show_alert=False) -> None:
        self.answers.append((text, show_alert))


class QueueTestHistory:
    async def dialogue(self, _chat_id):
        raise AssertionError("approval resume must use its persisted dialogue")
