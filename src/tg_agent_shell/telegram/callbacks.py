"""One press: claiming its token, and running the action it names.

A press comes from an inline button, or from a link inside a screen's words. Both carry a
token that works once, and both draw what they lead to in place of the screen they were on.
Every action is dispatched through the one table the features declared, so no module here
holds a list of which screens exist.
"""

from __future__ import annotations

import html
import logging
import re
from collections import deque
from datetime import UTC, datetime
from time import monotonic

from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, update

from ..foundation.errors import DomainError
from ..foundation.kinds import MessageKind
from .chat import SCREEN_KINDS, screen_anchor, send_registered, send_toast
from .contributions import StartLink
from .model import CallbackToken
from .navigation import LINK_PREFIX
from .services import CallbackContext, Services

logger = logging.getLogger(__name__)

# A tap on a link starts the bot through the owner's own account, and Telegram rate limits
# that per account for hours at a time. This many taps inside the window earns a warning.
LINK_BURST_TAPS = 8
LINK_BURST_SECONDS = 10

_LINK_PAYLOAD = re.compile(rf"^{re.escape(LINK_PREFIX)}[A-Za-z0-9_-]{{1,24}}$")

_BURST_WARNING = (
    "⏳ That is a lot of link taps. Telegram counts them against your account, not this "
    "bot, and can stop opening any bot for hours."
)
# The tap reaches Telegram before it reaches the bot, so nothing here can prevent one.
# Saying what is happening is the only thing left.
_link_taps: deque[float] = deque(maxlen=LINK_BURST_TAPS)


class _Pressed:
    """Why a token could not be claimed: pressed before, or drawn by a run that ended."""

    def __init__(self, again: bool) -> None:
        self.again = again


async def _claim(services: Services, token_value: str) -> tuple[str, dict] | _Pressed:
    async with services.sessions() as session:
        claimed = (
            await session.execute(
                update(CallbackToken)
                .where(
                    CallbackToken.token == token_value,
                    CallbackToken.owner_id == services.owner_id,
                    CallbackToken.consumed_at.is_(None),
                )
                .values(consumed_at=datetime.now(UTC))
                .returning(CallbackToken.action, CallbackToken.payload)
            )
        ).one_or_none()
        if claimed is None:
            # A token that is still here was pressed a second time; one that is gone
            # belongs to a screen drawn by a run that has ended.
            again = await session.scalar(
                select(CallbackToken.token).where(CallbackToken.token == token_value)
            )
            await session.commit()
            return _Pressed(again is not None)
        await session.commit()
    action, payload = claimed
    return action, dict(payload or {})


async def _dispatch(context: CallbackContext) -> None:
    services = context.services
    handler = services.callback_actions.get(context.action)
    if handler is None:
        logger.warning("Unknown Telegram callback action: %s", context.action)
        await send_registered(
            context.message,
            services,
            "This action is no longer available. Reopen the screen.",
            kind=MessageKind.ERROR,
        )
        return

    # A screen that can be restored after a failure restores itself: the feature that drew
    # it is the only one that knows whether it is still answerable.
    try:
        await handler(context)
    except DomainError as error:
        await send_registered(
            context.message, services, html.escape(str(error)), kind=MessageKind.ERROR
        )
    except Exception:
        logger.exception(
            "Telegram callback failed: action=%s payload=%s", context.action, context.payload
        )
        await send_registered(
            context.message,
            services,
            "This action could not be finished. Reopen the screen and try again.",
            kind=MessageKind.ERROR,
        )


async def callback_token_handler(callback: CallbackQuery, services: Services) -> None:
    if not callback.message:
        return
    claimed = await _claim(services, callback.data.split(":", 1)[1])
    if isinstance(claimed, _Pressed):
        if claimed.again:
            await callback.answer("This action expired. Reopen the screen.", show_alert=True)
            return
        await callback.answer()
        await send_registered(
            callback.message,
            services,
            "This screen is out of date. Reopen it from the menu.",
            kind=MessageKind.ERROR,
        )
        return
    await callback.answer()
    action, payload = claimed
    await _dispatch(CallbackContext(callback.message, services, action, payload))


def _is_a_burst() -> bool:
    now = monotonic()
    _link_taps.append(now)
    return len(_link_taps) == _link_taps.maxlen and now - _link_taps[0] < LINK_BURST_SECONDS


async def open_screen_link(message: Message, services: Services, payload: str) -> None:
    """Act on a tap on a link inside a screen, in place of that screen.

    A screen is only ever the last thing in the chat, so the newest one is the screen the
    link was on. The command middleware left it alive because this link claims the tap.
    """
    claimed = await _claim(services, payload.removeprefix(LINK_PREFIX))
    if isinstance(claimed, _Pressed):
        await send_registered(
            message,
            services,
            "This link is out of date. Reopen the screen.",
            kind=MessageKind.ERROR,
            replace=False,
        )
        return
    screens = await services.chat.notes.outgoing(message.chat.id, kinds=SCREEN_KINDS)
    target = (
        screen_anchor(message.bot, message.chat.id, screens[0].message_id) if screens else message
    )
    action, args = claimed
    await _dispatch(CallbackContext(target, services, action, args))
    if _is_a_burst():
        await send_toast(message, services, _BURST_WARNING)


SCREEN_LINK = StartLink(
    claims=lambda payload: _LINK_PAYLOAD.fullmatch(payload) is not None, open=open_screen_link
)
