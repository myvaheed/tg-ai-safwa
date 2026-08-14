"""Telegram I/O: sending, editing and deleting screens, and registering every one of them."""

from __future__ import annotations

import html
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import CALLBACK_TOKEN_TTL_HOURS
from ..enums import MessageKind, ProposalStatus
from ..history import SUBSESSION_RESULT_HEADER, mark_kind, mark_message, register_message
from ..models import (
    CallbackToken,
    ChangeProposal,
    ProposalChange,
    TelegramMessage,
    UiSession,
)
from ._core import QueuedMessage, Services
from ._presentation import Page, proposal_change_summary, split_telegram_text

logger = logging.getLogger(__name__)


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
) -> Message:
    """Render a UI state, replacing an inline-action screen when possible.

    Command and ordinary-text handlers receive a user message, so their response
    remains a new bot message.  Callback handlers receive the bot's previous
    message and therefore update that message in place.  Text-entry actions opt
    out explicitly because their prompt must be a separate conversational turn.
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
    if should_replace:
        try:
            await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
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
                sent = await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        sent = await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)
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
        await message.bot.edit_message_text(
            marked_text,
            chat_id=message.chat.id,
            message_id=message_id,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
    except TelegramAPIError as error:
        if "message is not modified" not in str(error).casefold():
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
    """Ensure an interaction screen is never left active above new dialogue."""
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
                    TelegramMessage.message_id < message.message_id,
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
                    changes = list(
                        await session.scalars(
                            select(ProposalChange)
                            .where(ProposalChange.proposal_id == proposal.id)
                            .order_by(ProposalChange.position)
                        )
                    )
                    proposal.status = ProposalStatus.REJECTED.value
                    proposed = "\n".join(
                        f"• {html.escape(proposal_change_summary(change))}" for change in changes
                    )
                    await session.commit()
                    advisor = getattr(services, "advisor", None)
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
                        replacement = (
                            "<b>Proposal discarded</b>\n"
                            "You continued the conversation without saving it.\n\n" + proposed
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
    dialogue_text = f"{services.owner_name}:\n{request}"
    sent = await send_registered(
        message,
        services,
        f"<b>{html.escape(services.owner_name)}:</b>\n{html.escape(request)}",
        kind=MessageKind.DIALOGUE_USER,
        replace=False,
    )
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


async def delete_message_range(message: Message, first_id: int, last_id: int) -> None:
    if last_id < first_id:
        return
    for offset in range(first_id, last_id + 1, 100):
        await message.bot.delete_messages(
            chat_id=message.chat.id,
            message_ids=list(range(offset, min(offset + 100, last_id + 1))),
        )


async def send_subsession_result(
    message: Message, services: Services, initial_request: str, result: str
) -> None:
    payload = f"{initial_request.strip()}\n\nSubsession result\n{result.strip()}"
    for index, chunk in enumerate(split_telegram_text(payload)):
        header = (
            SUBSESSION_RESULT_HEADER if index == 0 else f"{SUBSESSION_RESULT_HEADER} (continued)"
        )
        marked_text, event_id = mark_message(
            f"{header}\n{html.escape(chunk)}", MessageKind.SUBSESSION_RESULT
        )
        sent = await message.bot.send_message(
            message.chat.id,
            marked_text,
            parse_mode=ParseMode.HTML,
        )
        async with services.sessions() as session:
            await register_message(
                session,
                sent.chat.id,
                sent.message_id,
                "out",
                MessageKind.SUBSESSION_RESULT,
                event_id=event_id,
            )
            await session.commit()
