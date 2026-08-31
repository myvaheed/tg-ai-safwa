"""Safwa's side of `telegram_llm`'s chat host: its kinds, its buttons, its own screens."""

from __future__ import annotations

import html
import logging
import secrets
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_llm import ChatHost, Note

from ..constants import TOAST_SECONDS
from ..enums import MessageKind
from ..features.continuity.use_cases import record_summary
from ..features.proposals.model import BatchDecision
from ..history import MARKS, TelegramNotes
from ..models import CallbackToken, UiSession
from ._core import QueuedMessage, Services
from ._presentation import Page, proposal_outcome_text

logger = logging.getLogger(__name__)

# Every screen kind: a message the owner can still press something on.
_SCREEN_KINDS = frozenset(
    {
        MessageKind.DASHBOARD.value,
        MessageKind.CARD_EDITOR.value,
        MessageKind.APPROVAL.value,
    }
)


def _host(services: Services) -> ChatHost:
    return ChatHost(TelegramNotes(services.sessions), MARKS)


async def token_button(
    session: AsyncSession,
    owner_id: int,
    text: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> InlineKeyboardButton:
    """One button that works once, minted in the transaction that draws its screen."""
    token = secrets.token_urlsafe(9)
    session.add(
        CallbackToken(
            token=token,
            owner_id=owner_id,
            action=action,
            payload=payload or {},
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
    event_id: str | None = None,
    replace: bool | None = None,
    rich: bool = False,
) -> Message:
    return await _host(services).send(
        message,
        text,
        kind=kind.value,
        markup=markup,
        related_id=related_id,
        event_id=event_id,
        replace=replace,
        rich=rich,
    )


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
    await _host(services).edit(
        message,
        message_id,
        text,
        kind=kind.value,
        markup=markup,
        related_id=related_id,
        rich=rich,
    )


async def delete_text_input(message: Message, services: Services) -> bool:
    """Text entered into a field is operational UI input, not dialogue history."""
    return await _host(services).remove_incoming(message, kind=MessageKind.UI_INPUT.value)


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
    """Leave exactly one interaction screen live: the one this event belongs to."""

    async def freeze(screen: Note) -> tuple[str, str] | None:
        return await _interrupted_review(services, screen)

    await _host(services).leave_one_screen(message, kinds=_SCREEN_KINDS, freeze=freeze)
    async with services.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        await session.commit()


async def _interrupted_review(services: Services, screen: Note) -> tuple[str, str] | None:
    """A review the owner walked away from, frozen into what became of it.

    Every other screen is only a state and goes; a review is a question that was asked, so
    the chat has to keep saying it was asked and how it ended.
    """
    advisor = getattr(services, "advisor", None)
    if advisor is None or screen.kind != MessageKind.APPROVAL.value:
        return None
    review = advisor.reviews.proposal(screen.related_id)
    if review is None:
        return None
    async with services.sessions() as session:
        description = await advisor.describe_proposal(session, review.id)
    advisor.reviews.end_proposal(review.id)
    progress = await advisor.cancel_approval_for_proposal(review.id)
    if progress:
        # Earlier items in the queue may already be saved, and this frozen screen becomes
        # assistant history.  Report the whole request.
        text = (
            "<b>Request interrupted</b>\n"
            "You continued the conversation, so the remaining proposals were discarded.\n\n"
            + html.escape(progress)
        )
    else:
        text = proposal_outcome_text(
            BatchDecision.DISCARDED,
            description.summary,
            description.fields,
            notice="You continued the conversation without saving it.",
        )
    return text, MessageKind.DIALOGUE_ASSISTANT.value


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


async def send_prose(
    message: Message,
    services: Services,
    text: str,
    *,
    kind: MessageKind,
    event_id: str | None = None,
    replace: bool | None = None,
) -> Message:
    return await _host(services).send_parts(
        message, text, kind=kind.value, event_id=event_id, replace=replace
    )


async def send_owner_turn(message: Message, services: Services, text: str) -> Message:
    """Post as `DIALOGUE_USER` words that did not reach the chat as owner text."""
    return await _host(services).relay(
        message,
        owner_display_name(message, services),
        text,
        kind=MessageKind.DIALOGUE_USER.value,
    )


async def delete_screen(message: Message, services: Services, message_id: int) -> None:
    await _host(services).remove_screen(message, message_id)


async def send_toast(message: Message, services: Services, text: str) -> None:
    """A Toast is `STATUS`, so it never becomes dialogue."""
    await _host(services).toast(
        message, text, kind=MessageKind.STATUS.value, seconds=TOAST_SECONDS
    )


async def discard_toast(message: Message, services: Services) -> None:
    await _host(services).discard_toast(message)


async def discard_stale_status(bot: Bot, services: Services, chat_id: int) -> None:
    """Take back the Toasts and progress lines the process died under."""
    await _host(services).discard_stale(bot, chat_id, kind=MessageKind.STATUS.value)


async def send_summary(
    message: Message, services: Services, text: str, covered_id: int
) -> None:
    """Post one Summary and move the cut place `recent` reads back to.

    The cut place is the **last** part, which is where the backwards read meets it.
    """
    sent = await send_prose(
        message, services, html.escape(text), kind=MessageKind.SUMMARY, replace=False
    )
    async with services.sessions() as session:
        await record_summary(
            session, message_id=sent.message_id, covered_id=covered_id, text=text
        )
        await session.commit()
