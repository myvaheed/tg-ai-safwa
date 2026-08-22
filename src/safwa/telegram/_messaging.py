"""Telegram I/O: sending, editing and deleting screens, and registering every one of them."""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import CALLBACK_TOKEN_TTL_HOURS, TOAST_SECONDS
from ..enums import MessageKind, ProposalStatus
from ..features.continuity.use_cases import record_summary
from ..history import mark_kind, mark_message, register_message
from ..models import (
    CallbackToken,
    ChangeProposal,
    TelegramMessage,
    UiSession,
)
from ._core import QueuedMessage, Services
from ._presentation import Page, proposal_outcome_text, split_telegram_text

logger = logging.getLogger(__name__)

# Two taps arriving together would otherwise edit the same message at once, and Telegram
# answers the loser with "canceled by new edit message request" instead of drawing it.  Only
# edits contend: a new message cannot be cancelled by another, so sending never waits here.
_edit_lock = asyncio.Lock()


async def token_button(
    session: AsyncSession,
    owner_id: int,
    text: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> InlineKeyboardButton:
    token = secrets.token_urlsafe(9)
    session.add(
        CallbackToken(
            token=token,
            owner_id=owner_id,
            action=action,
            payload=payload or {},
            expires_at=datetime.now(UTC) + timedelta(hours=CALLBACK_TOKEN_TTL_HOURS),
        )
    )
    return InlineKeyboardButton(text=text, callback_data=f"cb:{token}")


async def send_registered(
    message: Message,
    services: Services,
    text: str,
    *,
    kind: MessageKind,
    markup: InlineKeyboardMarkup | None = None,
    related_id: int | None = None,
    replace: bool | None = None,
    rich: bool = False,
) -> Message:
    """Render a UI state, replacing an inline-action screen when possible.

    Command and ordinary-text handlers receive a user message, so their response
    remains a new bot message. Callback handlers receive the bot's previous
    message and therefore update that message in place. Callers only opt out for
    intentionally additive history messages.

    `rich` reads `text` as Rich HTML — a block dialect with tables, sent as a rich message
    instead of a parse-mode one. Marking and registration stay the same for both.
    """
    should_replace = (
        bool(message.from_user and message.from_user.is_bot) if replace is None else replace
    )
    visible_text = text
    event_id: str | None = None
    if should_replace:
        async with services.sessions() as session:
            stored = await session.scalar(
                select(TelegramMessage).where(
                    TelegramMessage.chat_id == message.chat.id,
                    TelegramMessage.message_id == message.message_id,
                )
            )
            event_id = stored.event_id if stored is not None else None
    text, event_id = mark_message(visible_text, kind, event_id=event_id)

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
            async with _edit_lock:
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
                text, event_id = mark_message(visible_text, kind)
                sent = await deliver_new(text)
    else:
        sent = await deliver_new(text)
    async with services.sessions() as session:
        await register_message(
            session,
            sent.chat.id,
            sent.message_id,
            "out",
            kind,
            related_id,
            event_id,
        )
        await session.commit()
    return sent


async def edit_registered_message(
    message: Message,
    services: Services,
    message_id: int,
    text: str,
    *,
    kind: MessageKind,
    markup: InlineKeyboardMarkup | None = None,
    related_id: int | None = None,
    rich: bool = False,
) -> None:
    """Replace a known bot UI message after consuming a separate user text message."""
    async with services.sessions() as session:
        stored = await session.scalar(
            select(TelegramMessage).where(
                TelegramMessage.chat_id == message.chat.id,
                TelegramMessage.message_id == message_id,
            )
        )
        event_id = stored.event_id if stored is not None else None
    marked_text, event_id = mark_message(text, kind, event_id=event_id)
    try:
        async with _edit_lock:
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
            # The screen went away without the bot removing it — clearing the chat leaves
            # its row behind — so the row goes too and the state is drawn as a new screen.
            async with services.sessions() as session:
                await session.execute(
                    delete(TelegramMessage).where(
                        TelegramMessage.chat_id == message.chat.id,
                        TelegramMessage.message_id == message_id,
                    )
                )
                await session.commit()
            await send_registered(
                message,
                services,
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
    async with services.sessions() as session:
        await register_message(
            session,
            message.chat.id,
            message_id,
            "out",
            kind,
            related_id,
            event_id,
        )
        await session.commit()


async def delete_text_input(message: Message, services: Services) -> bool:
    """Text entered into a field is operational UI input, not dialogue history."""
    async with services.sessions() as session:
        await register_message(
            session,
            message.chat.id,
            message.message_id,
            "in",
            MessageKind.UI_INPUT,
        )
        await session.commit()
    try:
        await message.delete()
    except TelegramAPIError as error:
        logger.warning("Could not delete UI field input %s: %s", message.message_id, error)
        return False
    return True


async def clear_message_markup(message: Message, message_id: int) -> None:
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=message_id,
            reply_markup=None,
        )
    except TelegramAPIError:
        pass


async def paging_row(
    session: AsyncSession,
    owner_id: int,
    page: Page,
    action: str,
    payload: dict[str, Any],
) -> list[list[InlineKeyboardButton]]:
    row: list[InlineKeyboardButton] = []
    if page.index > 0:
        row.append(
            await token_button(
                session, owner_id, "◀ Previous", action, {**payload, "page": page.index - 1}
            )
        )
    if page.index + 1 < page.count:
        row.append(
            await token_button(
                session, owner_id, "Next ▶", action, {**payload, "page": page.index + 1}
            )
        )
    return [row] if row else []


async def dismiss_prior_ui(message: Message, services: Services) -> None:
    """Leave exactly one interaction screen live: the one this event belongs to.

    The selector is "every other screen", not "every older screen" — a button pressed on a
    dashboard below an open proposal still has to answer that proposal.
    """
    ui_kinds = {
        MessageKind.DASHBOARD.value,
        MessageKind.CARD_EDITOR.value,
        MessageKind.APPROVAL.value,
    }
    async with services.sessions() as session:
        screens = list(
            await session.scalars(
                select(TelegramMessage)
                .where(
                    TelegramMessage.chat_id == message.chat.id,
                    TelegramMessage.direction == "out",
                    TelegramMessage.kind.in_(ui_kinds),
                    TelegramMessage.message_id != message.message_id,
                )
                .order_by(TelegramMessage.message_id.desc())
            )
        )

    for screen in screens:
        replacement: str | None = None
        if screen.kind == MessageKind.APPROVAL.value and screen.related_id:
            async with services.sessions() as session:
                proposal = await session.get(ChangeProposal, screen.related_id)
                if proposal is not None and proposal.status == "pending":
                    advisor = getattr(services, "advisor", None)
                    description = (
                        await advisor.describe_proposal(session, proposal.id)
                        if advisor is not None
                        else None
                    )
                    proposal.status = ProposalStatus.REJECTED.value
                    await session.commit()
                    progress = (
                        await advisor.cancel_approval_for_target("proposal", proposal.id)
                        if advisor is not None
                        else None
                    )
                    if progress:
                        # Earlier items in the queue may already be saved, and this frozen
                        # screen becomes assistant history.  Report the whole request.
                        replacement = (
                            "<b>Request interrupted</b>\n"
                            "You continued the conversation, so the remaining proposals were "
                            "discarded.\n\n" + html.escape(progress)
                        )
                    else:
                        replacement = proposal_outcome_text(
                            "discarded",
                            description.summary if description else "",
                            description.fields if description else None,
                            notice="You continued the conversation without saving it.",
                        )
        if replacement is not None:
            try:
                await message.bot.edit_message_text(
                    mark_kind(
                        replacement,
                        MessageKind.DIALOGUE_ASSISTANT,
                        event_id=screen.event_id,
                    ),
                    chat_id=message.chat.id,
                    message_id=screen.message_id,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramAPIError as error:
                logger.warning("Could not freeze proposal UI %s: %s", screen.message_id, error)
                try:
                    await message.bot.edit_message_reply_markup(
                        chat_id=message.chat.id,
                        message_id=screen.message_id,
                        reply_markup=None,
                    )
                except TelegramAPIError:
                    pass
                continue
            async with services.sessions() as session:
                await register_message(
                    session,
                    message.chat.id,
                    screen.message_id,
                    "out",
                    MessageKind.DIALOGUE_ASSISTANT,
                    screen.related_id,
                    screen.event_id,
                )
                await session.commit()
            continue

        await delete_screen(message, services, screen.message_id)

    async with services.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        await session.commit()


async def materialize_queued_dialogue(
    message: Message, services: Services
) -> tuple[Message, str] | None:
    """Replace transient queue notices with one durable user-role dialogue turn."""
    queued: list[QueuedMessage] = await services.guard.drain_queue()
    if not queued:
        return None
    request = "\n\n----\n\n".join(item.text.strip() for item in queued if item.text.strip())
    dialogue_text = f"{owner_display_name(message, services)}:\n{request}"
    sent = await send_owner_turn(message, services, request)
    placeholder_ids = [
        item.placeholder_message_id for item in queued if item.placeholder_message_id is not None
    ]
    if placeholder_ids:
        try:
            await message.bot.delete_messages(
                chat_id=message.chat.id,
                message_ids=placeholder_ids,
            )
        except TelegramAPIError as error:
            logger.warning("Could not remove queued-message placeholders: %s", error)
    return sent, dialogue_text


def owner_display_name(message: Message, services: Services) -> str:
    """What to call the owner in their own dialogue turns.

    The role is what the model reads, so it is always there; the Telegram name only
    qualifies it.  A Reminder anchor carries no real `from_user`, and a bot message is not
    the owner, so both fall back to the bare role.
    """
    user = message.from_user
    if user is not None and user.id == services.owner_id and user.full_name.strip():
        return f"User {user.full_name.strip()}"
    return "User"


async def send_owner_turn(message: Message, services: Services, text: str) -> Message:
    """Post the owner's words as their own dialogue turn, and return the last part.

    Used wherever those words did not reach the chat as owner text: a transcript, because
    a voice message carries none, and a queue drain, because the messages it holds were
    deleted.  Either can outgrow one Telegram message, so both are split here.
    """
    name = owner_display_name(message, services)
    sent: Message | None = None
    for index, part in enumerate(split_telegram_text(text)):
        # `dialogue()` merges consecutive user entries into one turn, so the name belongs
        # on the first part only; repeating it would read as several turns.
        head = f"<b>{html.escape(name)}:</b>\n" if index == 0 else ""
        sent = await send_registered(
            message,
            services,
            head + html.escape(part),
            kind=MessageKind.DIALOGUE_USER,
            replace=False,
        )
    if sent is None:
        raise ValueError("Refusing to post an empty dialogue turn")
    return sent


async def delete_screen(message: Message, services: Services, message_id: int) -> None:
    """Remove one bot screen, or at least its buttons when Telegram refuses to delete it."""
    try:
        await message.bot.delete_message(message.chat.id, message_id)
    except TelegramAPIError:
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=message_id,
                reply_markup=None,
            )
        except TelegramAPIError:
            pass
        return
    async with services.sessions() as session:
        stored = await session.scalar(
            select(TelegramMessage).where(
                TelegramMessage.chat_id == message.chat.id,
                TelegramMessage.message_id == message_id,
            )
        )
        if stored is not None:
            await session.delete(stored)
            await session.commit()


# One Toast at a time per chat: a burst of them would otherwise stack above the screen and
# push it out of sight, which is the one thing a Toast must not do.
_toasts: dict[int, tuple[int, asyncio.Task[None]]] = {}


async def send_toast(message: Message, services: Services, text: str) -> None:
    """Say one thing beside the screen and take it back after `TOAST_SECONDS`.

    A redraw carries its own notice through `with_notice`; a Toast is for what has to be
    said when the screen must stay exactly as it is.  It is `STATUS`, so it never becomes
    dialogue, and it leaves the screen the last message again once it expires.
    """
    await discard_toast(message, services)
    sent = await send_registered(
        message, services, text, kind=MessageKind.STATUS, replace=False
    )
    _toasts[message.chat.id] = (
        sent.message_id,
        asyncio.create_task(
            _expire_toast(message, services, sent.message_id), name="toast-expiry"
        ),
    )


async def discard_toast(message: Message, services: Services) -> None:
    """Remove the live Toast now. Its timer is cancelled, so it is deleted exactly once."""
    live = _toasts.pop(message.chat.id, None)
    if live is None:
        return
    message_id, task = live
    task.cancel()
    await delete_screen(message, services, message_id)


async def discard_stale_status(bot: Bot, services: Services, chat_id: int) -> None:
    """Take back the Toasts and progress lines the process died under.

    Startup is the one moment that can tell a stale one from a live one, because none is
    live yet.
    """
    async with services.sessions() as session:
        stale = list(
            await session.scalars(
                select(TelegramMessage).where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.direction == "out",
                    TelegramMessage.kind == MessageKind.STATUS.value,
                )
            )
        )
        for stored in stale:
            with suppress(TelegramAPIError):
                await bot.delete_message(chat_id, stored.message_id)
            await session.delete(stored)
        await session.commit()


async def _expire_toast(message: Message, services: Services, message_id: int) -> None:
    await asyncio.sleep(TOAST_SECONDS)
    _toasts.pop(message.chat.id, None)
    await delete_screen(message, services, message_id)


async def send_summary(
    message: Message, services: Services, text: str, covered_id: int
) -> None:
    """Post one Summary and move the cut place `recent` reads back to."""
    marked_text, event_id = mark_message(html.escape(text), MessageKind.SUMMARY)
    sent = await message.answer(marked_text)
    async with services.sessions() as session:
        await register_message(
            session,
            sent.chat.id,
            sent.message_id,
            "out",
            MessageKind.SUMMARY,
            event_id=event_id,
        )
        await record_summary(
            session, message_id=sent.message_id, covered_id=covered_id, text=text
        )
        await session.commit()
