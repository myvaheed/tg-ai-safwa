"""Safwa's side of the chat host: its kinds, its buttons, its own screens.

`telegram_llm` puts a message in the chat and takes it back; what the kinds mean, which
of them is a screen, and what a screen the owner walked away from should say instead are
all Safwa's, and they are here."""

from __future__ import annotations

import html
import logging
import secrets
from typing import Any

from aiogram import Bot
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_llm import Note

from ..constants import TOAST_SECONDS
from ..enums import MessageKind
from ..features.continuity.use_cases import record_summary
from ..features.proposals.model import BatchDecision
from ..features.proposals.render import proposal_outcome_text
from .layout import Page
from .model import CallbackToken, UiSession
from .services import Services

logger = logging.getLogger(__name__)

_SCREEN_KINDS = frozenset(
    {
        MessageKind.DASHBOARD.value,
        MessageKind.EDITOR.value,
        MessageKind.APPROVAL.value,
    }
)


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
    return await services.chat.send(
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
    await services.chat.edit(
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
    return await services.chat.remove_incoming(message, kind=MessageKind.UI_INPUT.value)


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

    await services.chat.leave_one_screen(message, kinds=_SCREEN_KINDS, freeze=freeze)
    async with services.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        await session.commit()


async def _interrupted_review(services: Services, screen: Note) -> tuple[str, str] | None:
    """A review the owner walked away from, frozen into what became of it.

    Every other screen is only a state and goes; a review is a question that was asked, so
    the chat has to keep saying it was asked and how it ended.
    """
    if screen.kind != MessageKind.APPROVAL.value:
        return None
    advisor = services.advisor
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
    return await services.chat.send_parts(
        message, text, kind=kind.value, event_id=event_id, replace=replace
    )


async def send_owner_turn(message: Message, services: Services, text: str) -> Message:
    """Post as `DIALOGUE_USER` words that did not reach the chat as owner text."""
    return await services.chat.relay(
        message,
        owner_display_name(message, services),
        text,
        kind=MessageKind.DIALOGUE_USER.value,
    )


async def delete_screen(message: Message, services: Services, message_id: int) -> None:
    await services.chat.remove_screen(message, message_id)


# `UI_INPUT` keeps it out of the conversation, and `/cancel` is tappable as written.
TURN_NOTICE = "⏳ Safwa is writing an answer.\n/cancel to stop it."


async def open_turn_notice(message: Message, services: Services) -> None:
    """Say in the chat that an answer is being written, once for the whole turn."""
    sent = await send_registered(
        message, services, TURN_NOTICE, kind=MessageKind.UI_INPUT, replace=False
    )
    services.turn.notice_shown(message.message_id, sent.message_id)


async def remove_turn_notice(
    message: Message, services: Services, notice: int | None
) -> None:
    if notice is not None:
        await delete_screen(message, services, notice)


async def end_turn(message: Message, services: Services) -> None:
    """Give the turn back and take its notice out of the chat, in that order."""
    await remove_turn_notice(message, services, services.turn.end(message.message_id))


async def send_toast(message: Message, services: Services, text: str) -> None:
    """A Toast is `STATUS`, so it never becomes dialogue."""
    await services.chat.toast(
        message, text, kind=MessageKind.STATUS.value, seconds=TOAST_SECONDS
    )


async def discard_toast(message: Message, services: Services) -> None:
    await services.chat.discard_toast(message)


async def discard_stale_status(bot: Bot, services: Services, chat_id: int) -> None:
    """Take back the Toasts and progress lines the process died under."""
    await services.chat.discard_stale(bot, chat_id, kind=MessageKind.STATUS.value)


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
