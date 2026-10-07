"""The buttons and links a screen is left by, built from `Place`s.

A screen builds `here` from the `back` it was handed. A button into another screen carries
`here.child(...)`; a button that only redraws this screen, or changes something and redraws
it, carries this screen's own `back`; `back_button(back)` closes the screen. A link inside
a screen's words is the same button in another shape (`place_link`). No screen knows who
opens it, and nothing about the way back is kept beside the chat, so a screen goes back
where it was drawn to go however long ago that was.
"""

from __future__ import annotations

from dataclasses import replace

from aiogram.types import InlineKeyboardButton, Message
from sqlalchemy.ext.asyncio import AsyncSession

from .chat import mint_token, token_button
from .contributions import HOME_NAV
from .layout import menu_row, start_link
from .place import Place
from .services import CallbackContext, Services

# A deep link whose payload is this and a token opens the token's Place in place of the
# screen the link is on.
LINK_PREFIX = "go-"


async def place_button(
    session: AsyncSession, owner_id: int, text: str, place: Place
) -> InlineKeyboardButton:
    """A button that draws `place` in place of the screen it is on."""
    return await token_button(session, owner_id, text, place.action, place.payload)


async def back_button(
    session: AsyncSession, owner_id: int, back: Place | None
) -> InlineKeyboardButton:
    """`↩️ Back` to `back`; with nothing behind the screen, `↩️ Menu` (SC-BACK-012)."""
    if back is None:
        return menu_row()[0]
    return await place_button(session, owner_id, "↩️ Back", back)


async def place_link(
    session: AsyncSession, services: Services, text: str, place: Place
) -> str:
    """`text`, already HTML, as a link that draws `place` in place of its screen."""
    token = await mint_token(session, services.owner_id, place.action, place.payload)
    return start_link(services.bot_username, text, LINK_PREFIX + token)


async def open_home(message: Message, services: Services) -> None:
    """Draw the menu screen, whichever feature owns it. The shell holds no list of them."""
    handler = next(screen.handler for screen in services.commands if screen.nav == HOME_NAV)
    await handler(message, services)


async def go(context: CallbackContext, place: Place | None, *, notice: str | None = None) -> None:
    """Draw `place` in place of the screen, as its button would. None is the menu."""
    handler = context.services.callback_actions.get(place.action) if place else None
    if place is None or handler is None:
        await open_home(context.message, context.services)
        return
    payload = place.payload | ({"notice": notice} if notice is not None else {})
    await handler(replace(context, action=place.action, payload=payload))
