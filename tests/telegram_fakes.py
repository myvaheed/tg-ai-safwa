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

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
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
        # What was sent without a notification.
        self.silent: list[str] = []
        self.published_commands: list[list[str]] = []
        # The files the owner's photos stand for, by file id, and every one fetched.
        self.files: dict[str, bytes] = {}
        self.downloads: list[str] = []
        # Every photo message the bot sent, as the list of what each carried.
        self.photos_sent: list[list[object]] = []
        # Telegram no longer knowing the file ids it handed out, as after a move to another bot.
        self.forgot_file_ids = False
        # Every change of the buttons alone, by message.
        self.keyboards: list[tuple[int, InlineKeyboardMarkup | None]] = []
        # Every message redrawn through the Bot API, with the buttons it was given.
        self.edited: list[tuple[int, InlineKeyboardMarkup | None]] = []

    async def download(self, file_id, destination):
        self.downloads.append(file_id)
        destination.write(self.files[file_id])
        destination.seek(0)
        return destination

    async def send_chat_action(self, _chat_id, _action) -> None:
        self.typing_calls += 1

    async def edit_message_text(
        self, text, *, chat_id, message_id, reply_markup=None, parse_mode=None
    ) -> None:
        del chat_id, parse_mode
        self.edits.append(text)
        self.edited.append((message_id, reply_markup))
        self.drawn.append(text)

    async def __call__(self, method, request_timeout=None):
        """An aiogram method a stand-in message sent, such as an edit of the screen a link
        was tapped on: the same record as the direct call."""
        del request_timeout
        if isinstance(method, EditMessageText):
            text = method.text if method.rich_message is None else method.rich_message.html
            await self.edit_message_text(
                text,
                chat_id=method.chat_id,
                message_id=method.message_id,
                reply_markup=method.reply_markup,
            )
            return True
        raise NotImplementedError(type(method).__name__)

    async def delete_message(self, chat_id, message_id) -> None:
        del chat_id
        self.deleted.append(message_id)

    async def delete_messages(self, *, chat_id, message_ids) -> None:
        del chat_id
        self.deleted.extend(message_ids)

    async def edit_message_reply_markup(self, *, chat_id, message_id, reply_markup=None) -> None:
        del chat_id
        self.keyboards.append((message_id, reply_markup))

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
        self.edit_date = None
        self.forward_origin = None
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
        self.caption: str | None = None
        self.photo: list[SimpleNamespace] | None = None
        self.media_group_id: str | None = None
        self.voice = self.audio = self.video_note = None

    async def edit_text(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        self.bot.drawn.append(text)
        return self

    async def answer(
        self, text, *, reply_markup=None, parse_mode=None, disable_notification=False
    ):
        del parse_mode
        if disable_notification:
            self.bot.silent.append(text)
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

    async def answer_photo(self, photo, *, caption=None, parse_mode=None):
        del parse_mode
        return (await self._send_photos([photo], caption))[0]

    async def answer_media_group(self, media):
        return await self._send_photos([item.media for item in media], media[0].caption)

    async def _send_photos(self, photos, caption):
        if self.bot.forgot_file_ids and any(isinstance(photo, str) for photo in photos):
            raise TelegramBadRequest(method=None, message="Bad Request: wrong file identifier")
        self.bot.photos_sent.append(list(photos))
        self.bot.drawn.append(caption or "[photo]")
        sent = []
        for photo in photos:
            message = QueueTestMessage(
                message_id=self.message_id + 1_000 + len(self.sent),
                owner_id=self.owner_id,
                answer_as_new=True,
                parent=self,
            )
            file_id = photo if isinstance(photo, str) else f"uploaded-{message.message_id}"
            message.photo = [SimpleNamespace(file_id=file_id, width=1280, height=960)]
            message.caption = caption if not sent else None
            self.sent.append(message)
            sent.append(message)
        return sent

    async def delete(self) -> None:
        self.was_deleted = True

    def buttons(self) -> list[str]:
        markup = self.markups[-1]
        return [button.text for row in markup.inline_keyboard for button in row]


def owner_photo(
    parent: QueueTestMessage,
    message_id: int,
    *,
    caption: str | None = None,
    media_group_id: str | None = None,
    data: bytes = b"jpeg-",
) -> QueueTestMessage:
    """A photo the owner sent, in the three sizes Telegram keeps of one."""
    photo = QueueTestMessage(
        message_id=message_id, owner_id=parent.owner_id, is_bot=False, parent=parent
    )
    photo.answer_as_new = True
    photo.caption = caption
    photo.media_group_id = media_group_id
    photo.photo = [
        SimpleNamespace(file_id=f"{message_id}-s", width=320, height=240),
        SimpleNamespace(file_id=f"{message_id}-y", width=1280, height=960),
        SimpleNamespace(file_id=f"{message_id}-w", width=2560, height=1920),
    ]
    for size in photo.photo:
        parent.bot.files[size.file_id] = data + size.file_id.encode()
    return photo


class QueueTestCallback:
    def __init__(self, token: str, message: QueueTestMessage, *, prefix: str = "cb") -> None:
        # `cb:` is one spent token; `nav:` is a permanent screen name and spends nothing.
        self.data = f"{prefix}:{token}"
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text=None, *, show_alert=False) -> None:
        self.answers.append((text, show_alert))


class QueueTestHistory:
    async def dialogue(self, _chat_id):
        raise AssertionError("approval resume must use its persisted dialogue")
