"""Putting a message in the chat, and taking a screen back out of it.

Every message the bot sends leaves through here, so every one of them carries its kind mark
and leaves a note behind. One that does not is invisible to the window.

A screen is a message the person can still act on, and only one is ever live: drawing a new
one takes the others away. What a screen that is being taken away should say instead, if
anything, is the host's to write — the package only knows that it must stop being a screen.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable, Collection, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InputRichMessage, Message

from .marking import KindMarks
from .notes import Note, NoteStore
from .text import split_telegram_text

logger = logging.getLogger(__name__)

# What becomes of a screen that is not the live one: the text to freeze it into and the kind
# it is from then on, or None to take it out of the chat.
Freeze = Callable[[Note], Awaitable[tuple[str, str] | None]]

# How a Toast's timer is started.  The package never starts one itself: a timer it started
# would outlive the host's shutdown, because nothing outside would know it exists.
Spawn = Callable[[Coroutine[None, None, None], str], "asyncio.Task[None]"]


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
    marks: KindMarks
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
    ) -> Message:
        """Draw one state, replacing the screen this event came from when there is one.

        A handler answering the person's own message sends a new message; one answering a
        button press updates the screen the button was on. `replace` says so outright when
        the caller means a message to be added rather than a state to be redrawn.

        `rich` reads `text` as Rich HTML — a block dialect with tables, sent as a rich
        message instead of a parse-mode one. Marking and the note are the same for both.
        """
        should_replace = (
            bool(message.from_user and message.from_user.is_bot) if replace is None else replace
        )
        visible_text = text
        # A supplied event id belongs to a caller that owns the delivery record — one that
        # posts a new message rather than replacing one, so no stored id is looked up for it.
        if should_replace and event_id is None:
            stored = await self.notes.note(message.chat.id, message.message_id)
            event_id = stored.event_id if stored is not None else None
        text, event_id = self.marks.write(visible_text, kind, event_id=event_id)

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
            return await message.answer(body, reply_markup=markup, parse_mode=ParseMode.HTML)

        if should_replace:
            try:
                async with self.edit_lock:
                    await deliver_edit(text)
                sent = message
            except TelegramAPIError as error:
                # Telegram rejects a no-op edit.  It is still the same rendered state.
                if "message is not modified" in str(error).casefold():
                    sent = message
                else:
                    logger.warning(
                        "Could not replace Telegram UI message %s; sending a new screen: %s",
                        message.message_id,
                        error,
                    )
                    text, event_id = self.marks.write(visible_text, kind)
                    sent = await deliver_new(text)
        else:
            sent = await deliver_new(text)
        await self.notes.write(
            Note(
                chat_id=sent.chat.id,
                message_id=sent.message_id,
                direction="out",
                kind=kind,
                related_id=related_id,
                event_id=event_id,
            )
        )
        return sent

    async def send_parts(
        self,
        message: Message,
        text: str,
        *,
        kind: str,
        event_id: str | None = None,
        replace: bool | None = None,
    ) -> Message:
        """Put words in the chat in as many messages as Telegram needs, and return the last.

        Every part is marked and noted on its own, so the window reads all of them and
        `dialogue` merges them back into the one turn they were. Only the first part may
        replace a screen; the rest are always new messages below it.

        A caller's own delivery identifier names the **last** part, so it appears only once
        the whole of what was said is in the chat. A caller that reads it back to decide
        whether it still owes these words would otherwise call a truncated send delivered.
        """
        parts = split_telegram_text(text)
        if not parts:
            raise ValueError("Refusing to put an empty message in the chat")
        for index, part in enumerate(parts[:-1]):
            await self.send(
                message, part, kind=kind, replace=replace if index == 0 else False
            )
        return await self.send(
            message,
            parts[-1],
            kind=kind,
            event_id=event_id,
            replace=replace if len(parts) == 1 else False,
        )

    async def relay(self, message: Message, name: str, text: str, *, kind: str) -> Message:
        """Put words in the chat as the person's own turn, because they never arrived as one.

        A voice message carries no text, so the transcript is the only record of what was
        said, and it has to be in the chat under their name or the model never sees it.
        `dialogue` merges consecutive turns of one person, so the name is written once,
        before the split; repeating it per part would read as several turns.
        """
        return await self.send_parts(
            message,
            f"<b>{html.escape(name)}:</b>\n{html.escape(text)}",
            kind=kind,
            replace=False,
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
        event_id = stored.event_id if stored is not None else None
        marked_text, event_id = self.marks.write(text, kind, event_id=event_id)
        try:
            async with self.edit_lock:
                if rich:
                    await message.bot.edit_message_text(
                        rich_message=InputRichMessage(html=marked_text),
                        chat_id=message.chat.id,
                        message_id=message_id,
                        reply_markup=markup,
                    )
                else:
                    await message.bot.edit_message_text(
                        marked_text,
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
            )
        )

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
            text, kind = frozen
            try:
                await message.bot.edit_message_text(
                    self.marks.write(text, kind, event_id=screen.event_id)[0],
                    chat_id=message.chat.id,
                    message_id=screen.message_id,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramAPIError as error:
                logger.warning("Could not freeze screen %s: %s", screen.message_id, error)
                await clear_markup(message, screen.message_id)
                continue
            await self.notes.write(
                Note(
                    chat_id=message.chat.id,
                    message_id=screen.message_id,
                    direction="out",
                    kind=kind,
                    related_id=screen.related_id,
                    event_id=screen.event_id,
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
