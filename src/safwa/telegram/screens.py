from __future__ import annotations

import html
import logging
import re
from typing import Any

from aiogram.types import InlineKeyboardButton, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import DomainError
from ..enums import MessageKind
from ..history import CITATION_PATTERN, citation_payload, parse_citation_payload
from ..models import Card, Check, DiaryEntry, SavedRequest, Tag, Value
from ._core import Services
from ._messaging import send_registered
from ._presentation import diary_label
from .cards import render_card
from .checks import render_check
from .diary import render_diary
from .items import render_item_editor, render_saved_request

logger = logging.getLogger(__name__)

OPENABLE_MODELS: dict[str, Any] = {
    "card": Card,
    "check": Check,
    "tag": Tag,
    "value": Value,
    "request": SavedRequest,
    "diary": DiaryEntry,
}


async def open_item_screen(
    message: Message,
    services: Services,
    item_type: str,
    item_id: int,
    *,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
    replace: bool | None = None,
) -> None:
    """Show one item exactly as navigating to it manually would, buttons included."""
    if item_type == "card":
        await render_card(message, services, item_id, extra_rows=extra_rows, replace=replace)
    elif item_type == "check":
        await render_check(message, services, item_id, extra_rows=extra_rows, replace=replace)
    elif item_type in {"tag", "value"}:
        await render_item_editor(
            message,
            services,
            item_type,
            mode="view",
            item_id=item_id,
            extra_rows=extra_rows,
            replace=replace,
        )
    elif item_type == "request":
        await render_saved_request(
            message, services, item_id, extra_rows=extra_rows, replace=replace
        )
    elif item_type == "diary":
        await render_diary(message, services, item_id, extra_rows=extra_rows, replace=replace)
    else:
        raise DomainError(f"{item_type.title()} has no screen to open")


async def open_citation(message: Message, services: Services, payload: str) -> None:
    """Open the item a cited deep link points at, as its own message."""
    target = parse_citation_payload(payload)
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

    A Diary day is also *named* here rather than by the model, so the date and the score on
    the link are always the saved ones.
    """
    matches = list(CITATION_PATTERN.finditer(text))
    if not matches:
        return text
    live: dict[tuple[str, int], str | None] = {}
    if services.bot_username:
        for item_type, item_id in {(match[2], int(match[3])) for match in matches}:
            item = await session.get(OPENABLE_MODELS[item_type], item_id)
            if item is None or getattr(item, "archived_at", None) is not None:
                continue
            live[(item_type, item_id)] = (
                diary_label(item.entry_date, item.feeling_score)
                if item_type == "diary"
                else None
            )

    def build(match: re.Match[str]) -> str:
        label, item_type, item_id = match[1], match[2], int(match[3])
        if (item_type, item_id) not in live:
            return label
        link = f"https://t.me/{services.bot_username}?start={citation_payload(item_type, item_id)}"
        override = live[(item_type, item_id)]
        shown = html.escape(override) if override is not None else label
        return f'<a href="{link}">{shown}</a>'

    return CITATION_PATTERN.sub(build, text)


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
