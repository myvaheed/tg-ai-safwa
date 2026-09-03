"""One inline button: claiming its token, running the action, and going back.

A screen records how to return to it as the callback action that draws it plus that
action's payload, so leaving a screen is a dispatch through the same table every button
goes through, and no module holds a list of which screens exist.
"""

from __future__ import annotations

import html
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from aiogram import F
from aiogram.types import CallbackQuery
from sqlalchemy import select, update

from ..adapters.kinds import MessageKind
from ..foundation.errors import DomainError
from .chat import send_registered
from .commands import open_home
from .model import CallbackToken
from .services import CallbackContext, Services, router

logger = logging.getLogger(__name__)


async def go_back(
    context: CallbackContext,
    back: dict[str, Any] | None,
    *,
    notice: str | None = None,
) -> None:
    """Redraw the screen a `back` payload names. Naming none of them is the menu."""
    action = (back or {}).get("action")
    handler = context.services.callback_actions.get(action) if action else None
    if handler is None:
        await open_home(context.message, context.services)
        return
    payload = {key: value for key, value in back.items() if key != "action"}
    if notice is not None:
        payload["notice"] = notice
    await handler(replace(context, action=action, payload=payload))


async def go_back_action(context: CallbackContext) -> None:
    """The `↩️ Back` button of any screen that was handed where it came from."""
    await go_back(context, context.payload.get("back"))


@router.callback_query(F.data.startswith("cb:"))
async def callback_token_handler(callback: CallbackQuery, services: Services) -> None:
    if not callback.message:
        return
    token_value = callback.data.split(":", 1)[1]
    async with services.sessions() as session:
        now = datetime.now(UTC)
        claimed = (
            await session.execute(
                update(CallbackToken)
                .where(
                    CallbackToken.token == token_value,
                    CallbackToken.owner_id == services.owner_id,
                    CallbackToken.consumed_at.is_(None),
                )
                .values(consumed_at=now)
                .returning(CallbackToken.action, CallbackToken.payload)
            )
        ).one_or_none()
        if claimed is None:
            # A token that is still here was pressed a second time; one that is gone
            # belongs to a screen drawn by a run of Safwa that has ended.
            pressed_again = await session.scalar(
                select(CallbackToken.token).where(CallbackToken.token == token_value)
            )
            await session.commit()
            if pressed_again is not None:
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
        action, payload = claimed
        await session.commit()

    context = CallbackContext(callback, services, action, dict(payload or {}))
    await callback.answer()
    handler = services.callback_actions.get(action)
    if handler is None:
        logger.warning("Unknown Telegram callback action: %s", action)
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
        logger.exception("Telegram callback failed: action=%s payload=%s", action, payload)
        await send_registered(
            context.message,
            services,
            "Safwa could not finish this action. Reopen the screen and try again.",
            kind=MessageKind.ERROR,
        )
