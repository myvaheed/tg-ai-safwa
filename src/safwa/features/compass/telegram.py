"""The Constellation the Dashboard's button sends: the sky of Values, in place of the screen it
was asked from, with a message below it that leads to each Value's orbits, from there to each
Goal's branch, and back. Each file stays in the chat like a message, to be opened and zoomed
into after the Dashboard is back."""

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
    place_button,
    send_registered,
)

from ...foundation.charts import cut
from .charts import draw
from .constellation import read_branch, read_orbits, read_sky

# How much of a Goal's name its button carries.
DOOR_CHARS = 40


async def render_constellation(
    message: Message,
    services: Services,
    back: Place | None,
    *,
    value: int | None = None,
    goal: int | None = None,
) -> None:
    """The sky, a Value's orbits or a Goal's branch. Every way down keeps the way back to the
    screen the sky was opened from."""
    now = utcnow()
    async with services.sessions() as session:
        if value is not None:
            picture = await read_orbits(session, value, now)
        elif goal is not None:
            picture = await read_branch(session, goal, now)
        else:
            picture = await read_sky(session, now)
        if picture is None:
            raise DomainError(
                "Nothing to draw yet: add a Value or a Goal first." if value is None and goal is None
                else "That is no longer there to draw."
            )
        doors = [
            await place_button(
                session, services.owner_id, cut(label, DOOR_CHARS),
                Place("constellation", {kind: item}, back),
            )
            for kind, item, label in picture.doors
        ]
        rows = [doors[index : index + 2] for index in range(0, len(doors), 2)]
        rows.append([await back_button(session, services.owner_id, back)])
        await session.commit()
    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_DOCUMENT)
    image = await asyncio.to_thread(draw, picture)
    # A file cannot go above a message already in the chat, so the screen it was asked from
    # goes, and the message that leads on comes below it.
    await dismiss_prior_ui(message, services)
    if message.from_user and message.from_user.is_bot:
        await services.chat.remove_screen(message, message.message_id)
    await services.chat.send_document(
        message,
        BufferedInputFile(image, filename="constellation.png"),
        kind=MessageKind.RECEIPT.value,
    )
    flagged = sum(star.flagged for star in picture.stars) + sum(
        node.flagged for node in picture.nodes
    )
    await send_registered(
        message,
        services,
        f"<b>🌌 {html.escape(picture.title)}</b>\nOpen the file to zoom in."
        + (f"\nRinged in red: {flagged}. Ask the Advisor about them." if flagged else ""),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        replace=False,
    )


async def _on_constellation(context: CallbackContext) -> None:
    args = context.place.args
    await render_constellation(
        context.message, context.services, context.back,
        value=args.get("value"), goal=args.get("goal"),
    )


COMPASS_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {"constellation": _on_constellation}
