"""The Constellation the Dashboard's button sends: the whole tree as one picture, in place of
the screen it was asked from, with a message below it that leads back. The file stays in the
chat like a message, to be opened and zoomed into after the Dashboard is back."""

from __future__ import annotations

import asyncio
import html

from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    back_button,
    dismiss_prior_ui,
    send_registered,
)

from ..schedules.api import workspace_zone
from .charts import draw_sky
from .constellation import read_sky


async def render_constellation(message: Message, services: Services, back: Place | None) -> None:
    async with services.sessions() as session:
        sky = await read_sky(session)
        if sky is None:
            raise DomainError("Nothing to draw yet: add a Value or a Goal first.")
        today = utcnow().astimezone(await workspace_zone(session)).date()
        rows = [[await back_button(session, services.owner_id, back)]]
        await session.commit()
    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_DOCUMENT)
    picture = await asyncio.to_thread(draw_sky, sky, today)
    # A file cannot go above a message already in the chat, so the screen it was asked from
    # goes, and the message that leads back comes below it.
    await dismiss_prior_ui(message, services)
    if message.from_user and message.from_user.is_bot:
        await services.chat.remove_screen(message, message.message_id)
    await services.chat.send_document(
        message,
        BufferedInputFile(picture, filename="constellation.png"),
        kind=MessageKind.RECEIPT.value,
    )
    flagged = sum(star.flagged for star in sky.stars)
    await send_registered(
        message,
        services,
        f"<b>🌌 {html.escape(sky.title)}</b>\nOpen the file to zoom in."
        + (f"\nRinged in red: {flagged}. Ask the Advisor about them." if flagged else ""),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        replace=False,
    )


async def _on_constellation(context: CallbackContext) -> None:
    await render_constellation(context.message, context.services, context.back)


COMPASS_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {"constellation": _on_constellation}
