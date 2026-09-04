from __future__ import annotations

import html
import logging
import re

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from ..foundation.errors import DomainError
from ..foundation.kinds import MessageKind
from .chat import send_registered
from .services import Services

logger = logging.getLogger(__name__)


async def open_item_screen(
    message: Message,
    services: Services,
    item_type: str,
    item_id: int,
    *,
    replace: bool | None = None,
) -> None:
    """Show one item exactly as navigating to it manually would, buttons included."""
    spec = services.screens.by_type.get(item_type)
    if spec is None:
        raise DomainError(f"{item_type.title()} has no screen to open")
    await spec.open(message, services, item_id, replace=replace)


async def open_citation(message: Message, services: Services, payload: str) -> None:
    """Open the item a cited deep link points at, as its own message."""
    target = services.screens.parse_payload(payload)
    if target is None:
        await report_open_failure(message, services, DomainError("that link is not a Safwa item"))
        return
    item_type, item_id = target
    try:
        await open_item_screen(message, services, item_type, item_id, replace=False)
    except DomainError as error:
        await report_open_failure(message, services, error)


async def render_citations(session: AsyncSession, services: Services, text: str) -> str:
    """Turn the advisor's `[text](card:12)` citations into links that open the item.

    ``text`` is already HTML-escaped: only the href is added, and it is built here from a
    validated id, never taken from the model. An item that no longer exists loses its link
    instead of leaving a dead one in a message that stays in the chat for good.

    Live Cards and saved item types are also named here rather than by the model.  That keeps
    every link compact and gives its metadata directly from the current saved item.
    """
    screens = services.screens
    matches = list(screens.citation.finditer(text))
    if not matches:
        return screens.markup.sub(lambda match: match[1], text)
    live: dict[tuple[str, int], str | None] = {}
    if services.bot_username:
        for item_type, item_id in {(match[2], int(match[3])) for match in matches}:
            spec = screens.by_type[item_type]
            item = await session.get(spec.model, item_id)
            if item is None:
                continue
            live[(item_type, item_id)] = await spec.label(session, services, item)

    def build(match: re.Match[str]) -> str:
        label, item_type, item_id = match[1], match[2], int(match[3])
        if (item_type, item_id) not in live:
            return label
        payload = screens.payload(item_type, item_id)
        link = f"https://t.me/{services.bot_username}?start={payload}"
        override = live[(item_type, item_id)]
        shown = html.escape(override) if override is not None else label
        return f'<a href="{link}">{shown}</a>'

    # A target that is not an id — a stamp, a date, an invented number — never reached the
    # pass above, and raw Markdown must not stay in a message the chat keeps for good.
    return screens.markup.sub(lambda match: match[1], screens.citation.sub(build, text))


async def report_open_failure(
    message: Message, services: Services, error: Exception
) -> None:
    await send_registered(
        message,
        services,
        f"⚠️ Error while opening: {html.escape(str(error))}",
        kind=MessageKind.ERROR,
        replace=False,
    )
