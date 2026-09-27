"""Putting a message in the chat, and taking a screen back out of it.

Every message the bot sends leaves through here, so every one of them leaves a note behind,
words included. One that does not is invisible to the window.

A screen is a message the person can still act on, and only one is ever live: drawing a new
one takes the others away. What a screen that is being taken away should say instead, if
anything, is the host's to write — the package only knows that it must stop being a screen.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable, Collection, Coroutine, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
    InputRichMessage,
    Message,
)

from .notes import Note, NoteStore
from .text import split_telegram_text
from .window import SCAN_LIMIT

logger = logging.getLogger(__name__)

# The most photos Telegram puts in one album.
TELEGRAM_ALBUM_LIMIT = 10

# The most messages one `deleteMessages` call takes, and how long after it was sent a bot
# may still delete one.
TELEGRAM_DELETE_BATCH = 100
TELEGRAM_DELETE_WINDOW = timedelta(hours=48)

# What becomes of a screen that is not the live one: the text to freeze it into and the kind
# it is from then on, or None to take it out of the chat.
Freeze = Callable[[Note], Awaitable[tuple[str, str] | None]]

# How a Toast's timer is started.  The package never starts one itself: a timer it started
# would outlive the host's shutdown, because nothing outside would know it exists.
Spawn = Callable[[Coroutine[None, None, None], str], "asyncio.Task[None]"]


def _utc(moment: datetime) -> datetime:
    """A store may hand back a naive moment; it is UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def clear_markup(message: Message, message_id: int) -> None:
    """Leave the words standing and take the buttons off them."""
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=message_id,
            reply_markup=None,
        )
    except TelegramAPIError:
        pass


@dataclass(frozen=True)
class ChatHost:
    """The bot's side of the chat: what it says, and what it takes back."""

    notes: NoteStore
    spawn: Spawn = field(kw_only=True)
    # Two taps arriving together would otherwise edit the same message at once, and
    # Telegram answers the loser with "canceled by new edit message request" instead of
    # drawing it. Only edits contend: a new message cannot be cancelled by another, so
    # sending never waits here.
    edit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # One Toast at a time per chat: a burst of them would otherwise stack above the
    # screen and push it out of sight, which is the one thing a Toast must not do.
    toasts: dict[int, tuple[int, asyncio.Task[None]]] = field(default_factory=dict)

    async def send(
        self,
        message: Message,
        text: str,
        *,
        kind: str,
        markup: InlineKeyboardMarkup | None = None,
        related_id: int | None = None,
        event_id: str | None = None,
        replace: bool | None = None,
        rich: bool = False,
        silent: bool = False,
    ) -> Message:
        """Draw one state, replacing the screen this event came from when there is one.

        A handler answering the person's own message sends a new message; one answering a
        button press updates the screen the button was on. `replace` says so outright when
        the caller means a message to be added rather than a state to be redrawn.

        `rich` reads `text` as Rich HTML — a block dialect with tables, sent as a rich
        message instead of a parse-mode one. The note is the same for both. `silent` sends
        a new parse-mode message without a notification.
        """
        sent, event_id = await self._draw(
            message,
            text,
            markup=markup,
            event_id=event_id,
            replace=replace,
            rich=rich,
            silent=silent,
        )
        await self._note(sent, kind, event_id, text=text, related_id=related_id)
        return sent

    async def _draw(
        self,
        message: Message,
        text: str,
        *,
        markup: InlineKeyboardMarkup | None = None,
        event_id: str | None = None,
        replace: bool | None = None,
        rich: bool = False,
        silent: bool = False,
    ) -> tuple[Message, str]:
        """Put one message in the chat, and say which it is and under which delivery."""
        should_replace = (
            bool(message.from_user and message.from_user.is_bot) if replace is None else replace
        )
        # A supplied event id belongs to a caller that owns the delivery record — one that
        # posts a new message rather than replacing one, so no stored id is looked up for it.
        if should_replace and event_id is None:
            stored = await self.notes.note(message.chat.id, message.message_id)
            event_id = stored.event_id if stored is not None else None
        event_id = event_id or uuid4().hex

        async def deliver_edit(body: str) -> None:
            if rich:
                await message.edit_text(
                    rich_message=InputRichMessage(html=body), reply_markup=markup
                )
            else:
                await message.edit_text(body, reply_markup=markup, parse_mode=ParseMode.HTML)

        async def deliver_new(body: str) -> Message:
            if rich:
                return await message.answer_rich(
                    rich_message=InputRichMessage(html=body), reply_markup=markup
                )
            return await message.answer(
                body,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
                disable_notification=silent,
            )

        if not should_replace:
            return await deliver_new(text), event_id
        try:
            async with self.edit_lock:
                await deliver_edit(text)
            return message, event_id
        except TelegramAPIError as error:
            # Telegram rejects a no-op edit.  It is still the same rendered state.
            if "message is not modified" in str(error).casefold():
                return message, event_id
            logger.warning(
                "Could not replace Telegram UI message %s; sending a new screen: %s",
                message.message_id,
                error,
            )
            return await deliver_new(text), uuid4().hex

    async def _note(
        self,
        sent: Message,
        kind: str,
        event_id: str,
        *,
        text: str | None,
        related_id: int | None = None,
        reads_as: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        await self.notes.write(
            Note(
                chat_id=sent.chat.id,
                message_id=sent.message_id,
                direction="out",
                kind=kind,
                related_id=related_id,
                event_id=event_id,
                text=text,
                at=sent.date,
                reads_as=tuple(reads_as) if reads_as is not None else None,
            )
        )

    async def send_parts(
        self,
        message: Message,
        text: str,
        *,
        kind: str,
        event_id: str | None = None,
        replace: bool | None = None,
        reads_as: Sequence[Mapping[str, Any]] | None = None,
    ) -> Message:
        """Put words in the chat in as many messages as Telegram needs, and return the last.

        What was said is one message however many Telegram needs, so it is kept once: the
        first part holds all of its words, and `reads_as` when the model reads it as
        something else, and the rest hold none. Only the first part may replace a screen;
        the rest are always new messages below it.

        A caller's own delivery identifier names the **last** part, so it appears only once
        the whole of what was said is in the chat. A caller that reads it back to decide
        whether it still owes these words would otherwise call a truncated send delivered.
        """
        parts = split_telegram_text(text)
        if not parts:
            raise ValueError("Refusing to put an empty message in the chat")
        for index, part in enumerate(parts):
            first, last = index == 0, index == len(parts) - 1
            sent, delivery = await self._draw(
                message,
                part,
                event_id=event_id if last else None,
                replace=replace if first else False,
            )
            await self._note(
                sent,
                kind,
                delivery,
                text=text if first else None,
                reads_as=reads_as if first else None,
            )
        return sent

    async def send_photos(
        self,
        message: Message,
        photos: Sequence[str | InputFile],
        *,
        kind: str,
        caption: str | None = None,
        related_id: int | None = None,
    ) -> list[Message]:
        """Put photos in the chat as one message — a photo, or an album — and keep each.

        A photo is a Telegram file id or the file itself. The caption is HTML and goes under
        the first photo. An album is its photos alone: Telegram draws no buttons under one,
        so a screen puts its words and buttons in a message of their own below it. Each
        photo is kept with its kind and no words, so it is never part of the conversation.
        """
        if not 0 < len(photos) <= TELEGRAM_ALBUM_LIMIT:
            raise ValueError(f"An album holds 1 to {TELEGRAM_ALBUM_LIMIT} photos")
        if len(photos) == 1:
            sent = [
                await message.answer_photo(
                    photos[0], caption=caption, parse_mode=ParseMode.HTML
                )
            ]
        else:
            sent = list(
                await message.answer_media_group(
                    [
                        InputMediaPhoto(
                            media=photo,
                            caption=caption if index == 0 else None,
                            parse_mode=ParseMode.HTML,
                        )
                        for index, photo in enumerate(photos)
                    ]
                )
            )
        for photo in sent:
            await self._note(photo, kind, uuid4().hex, text=None, related_id=related_id)
        return sent

    async def relay(self, message: Message, name: str, text: str, *, kind: str) -> Message:
        """Put words in the chat as the person's own turn, because they never arrived as one.

        A voice message carries no text, so the transcript is the only record of what was
        said, and it has to be in the chat under their name. The name is for the person:
        the model reads the words alone, as it reads anything else they said.
        """
        return await self.send_parts(
            message,
            f"<b>{html.escape(name)}:</b>\n{html.escape(text)}",
            kind=kind,
            replace=False,
            reads_as=({"role": "user", "content": text},),
        )

    async def edit(
        self,
        message: Message,
        message_id: int,
        text: str,
        *,
        kind: str,
        markup: InlineKeyboardMarkup | None = None,
        related_id: int | None = None,
        rich: bool = False,
    ) -> None:
        """Redraw a known screen after consuming a separate message the person sent."""
        stored = await self.notes.note(message.chat.id, message_id)
        event_id = stored.event_id if stored is not None else uuid4().hex
        try:
            async with self.edit_lock:
                if rich:
                    await message.bot.edit_message_text(
                        rich_message=InputRichMessage(html=text),
                        chat_id=message.chat.id,
                        message_id=message_id,
                        reply_markup=markup,
                    )
                else:
                    await message.bot.edit_message_text(
                        text,
                        chat_id=message.chat.id,
                        message_id=message_id,
                        reply_markup=markup,
                        parse_mode=ParseMode.HTML,
                    )
        except TelegramAPIError as error:
            reason = str(error).casefold()
            if "message to edit not found" in reason:
                # The screen went away without the bot removing it — clearing the chat
                # leaves its note behind — so the note goes too and the state is drawn new.
                await self.notes.forget(message.chat.id, message_id)
                await self.send(
                    message,
                    text,
                    kind=kind,
                    markup=markup,
                    related_id=related_id,
                    replace=False,
                    rich=rich,
                )
                return
            if "message is not modified" not in reason:
                raise
        await self.notes.write(
            Note(
                chat_id=message.chat.id,
                message_id=message_id,
                direction="out",
                kind=kind,
                related_id=related_id,
                event_id=event_id,
                text=text,
            )
        )

    async def keep(
        self,
        message: Message,
        *,
        kind: str,
        reads_as: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Keep what the person said, as the chat shows it. Nothing else records it.

        A photo's words are its caption. What the model reads for a message whose words are
        not all of it — a photo's label — is `reads_as`.
        """
        await self.notes.write(
            Note(
                chat_id=message.chat.id,
                message_id=message.message_id,
                direction="in",
                kind=kind,
                text=html.escape(message.text or message.caption or ""),
                at=message.date,
                reads_as=tuple(reads_as) if reads_as is not None else None,
            )
        )

    async def amend(self, message: Message) -> None:
        """Take in the person's edit of words already kept.

        Only what `keep` took in has words to change: an edit to anything else the person
        sent changes nothing that is read back.
        """
        kept = await self.notes.note(message.chat.id, message.message_id)
        if kept is None or kept.direction != "in" or kept.text is None:
            return
        await self.notes.write(replace(kept, text=html.escape(message.text or "")))

    async def remove_incoming(self, message: Message, *, kind: str) -> bool:
        """Note what the person sent, then take it out of the chat.

        Words typed into a field are operating the bot rather than talking to it. The note
        outlives the message, so what the chat no longer shows is still accounted for.
        """
        await self.notes.write(
            Note(
                chat_id=message.chat.id,
                message_id=message.message_id,
                direction="in",
                kind=kind,
            )
        )
        try:
            await message.delete()
        except TelegramAPIError as error:
            logger.warning("Could not delete UI field input %s: %s", message.message_id, error)
            return False
        return True

    async def remove_screen(self, message: Message, message_id: int) -> None:
        """Take one bot message away, or at least its buttons when Telegram refuses.

        The note goes either way: a message with nothing left to press is no longer a
        screen, and one kept would be walked and stripped again on every later event.
        """
        try:
            await message.bot.delete_message(message.chat.id, message_id)
        except TelegramAPIError:
            await clear_markup(message, message_id)
        await self.notes.forget(message.chat.id, message_id)

    async def leave_one_screen(
        self,
        message: Message,
        *,
        kinds: Collection[str],
        freeze: Freeze,
    ) -> None:
        """Leave exactly one screen live: the one this event belongs to.

        The selector is "every other screen", not "every older screen" — a button pressed
        on a screen below an open one still has to answer the open one.
        """
        for screen in await self.notes.outgoing(message.chat.id, kinds=kinds):
            if screen.message_id == message.message_id:
                continue
            frozen = await freeze(screen)
            if frozen is None:
                await self.remove_screen(message, screen.message_id)
                continue
            await self.freeze_screen(message, screen, *frozen)

    async def clear(
        self,
        bot: Bot,
        chat_id: int,
        last: int,
        *,
        keep: Collection[str],
        first: int = 0,
    ) -> None:
        """Take every message from `first` to `last` out of the chat, whoever sent it.

        In a private chat both sides share one sequence of ids, so a range is everything
        in it, the person's messages and commands included; an id that is gone is skipped.
        It starts no earlier than the oldest kept message a bot may still delete. The bot's
        notes of the kinds in `keep` stay, because what was said is still read for a period
        after it left the chat. Every other one goes with its message; the person's own
        notes are their words, or of a message already taken out.
        """
        since = datetime.now(UTC) - TELEGRAM_DELETE_WINDOW
        deletable = [
            note.message_id
            for note in await self.notes.messages(chat_id, limit=SCAN_LIMIT)
            if note.at is not None and _utc(note.at) >= since
        ]
        ids = list(range(max(first, min(deletable, default=last + 1)), last + 1))
        for start in range(0, len(ids), TELEGRAM_DELETE_BATCH):
            try:
                await bot.delete_messages(
                    chat_id=chat_id, message_ids=ids[start : start + TELEGRAM_DELETE_BATCH]
                )
            except TelegramAPIError as error:
                logger.warning("Could not clear messages from %s: %s", ids[start], error)
        for note in await self.notes.outgoing(chat_id):
            if first <= note.message_id <= last and note.kind not in keep:
                await self.notes.forget(chat_id, note.message_id)

    async def freeze_screen(self, message: Message, screen: Note, text: str, kind: str) -> None:
        """Leave a screen standing as what became of it: these words, this kind, no buttons."""
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=screen.message_id,
                parse_mode=ParseMode.HTML,
            )
        except TelegramAPIError as error:
            logger.warning("Could not freeze screen %s: %s", screen.message_id, error)
            await clear_markup(message, screen.message_id)
            await self.notes.forget(message.chat.id, screen.message_id)
            return
        await self.notes.write(
            Note(
                chat_id=message.chat.id,
                message_id=screen.message_id,
                direction="out",
                kind=kind,
                related_id=screen.related_id,
                event_id=screen.event_id,
                text=text,
            )
        )

    async def toast(self, message: Message, text: str, *, kind: str, seconds: float) -> None:
        """Say one thing beside the screen and take it back after `seconds`.

        A redraw carries its own notice; a Toast is for what has to be said when the screen
        must stay exactly as it is. Its kind keeps it out of the conversation, and it leaves
        the screen the last message again once it expires.
        """
        await self.discard_toast(message)
        sent = await self.send(message, text, kind=kind, replace=False)
        self.toasts[message.chat.id] = (
            sent.message_id,
            self.spawn(self._expire_toast(message, sent.message_id, seconds), "toast-expiry"),
        )

    async def discard_toast(self, message: Message) -> None:
        """Take the live Toast back now. Its timer is cancelled, so it goes exactly once."""
        live = self.toasts.pop(message.chat.id, None)
        if live is None:
            return
        message_id, task = live
        task.cancel()
        await self.remove_screen(message, message_id)

    async def _expire_toast(self, message: Message, message_id: int, seconds: float) -> None:
        await asyncio.sleep(seconds)
        self.toasts.pop(message.chat.id, None)
        await self.remove_screen(message, message_id)

    async def discard_stale(self, bot: Bot, chat_id: int, *, kind: str) -> None:
        """Take back the transient messages the process died under.

        Startup is the one moment that can tell a stale one from a live one, because none
        is live yet.
        """
        for stale in await self.notes.outgoing(chat_id, kinds={kind}):
            with suppress(TelegramAPIError):
                await bot.delete_message(chat_id, stale.message_id)
            await self.notes.forget(chat_id, stale.message_id)
