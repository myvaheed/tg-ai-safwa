from __future__ import annotations

import html
import logging
import re
from typing import Any

from aiogram.types import InlineKeyboardButton, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import DomainError, card_progress, repeat_marker
from ..enums import CardKind, MessageKind
from ..features.diary.model import DiaryEntry
from ..features.diary.telegram import diary_label, render_diary
from ..features.saved_requests.use_cases import request_cards
from ..history import (
    CITATION_MARKUP,
    CITATION_PATTERN,
    citation_payload,
    parse_citation_payload,
)
from ..models import (
    Card,
    CardCategory,
    CardEnergyType,
    Check,
    SavedRequest,
    Tag,
    Value,
)
from ._core import Services
from ._messaging import send_registered
from ._presentation import CATEGORY_EMOJIS, ENERGY_EMOJIS, kind_emoji
from .cards import render_card
from .checks import render_check
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

CITATION_TITLE_LIMIT = 25


def _short_citation_title(value: str) -> str:
    """Keep a citation recognisable without letting it consume an advisor reply."""
    title = value.strip()
    return f"{title[: CITATION_TITLE_LIMIT - 1]}…" if len(title) > CITATION_TITLE_LIMIT else title


def _emoji_group(values: list[str], emojis: dict[str, str]) -> str:
    """Render a set of existing field icons in the product's established order."""
    present = set(values)
    return "".join(emoji for field, emoji in emojis.items() if field in present)


def _with_citation_fields(leading: str, fields: list[str]) -> str:
    return f"{leading} · {'·'.join(fields)}" if fields else leading


async def _card_citation_label(session: AsyncSession, card: Card) -> str:
    marker = await repeat_marker(session, card)
    leading = f"{kind_emoji(card.kind)} {_short_citation_title(card.title)}{marker}"
    if card.kind in {CardKind.GOAL.value, CardKind.IDEA.value}:
        progress = await card_progress(session, card.id)
        return _with_citation_fields(
            leading, [f"⚡{progress['completed_effort']}/{progress['total_effort']}"]
        )
    if card.kind != CardKind.ACTION.value:
        return leading

    categories = list(
        await session.scalars(
            select(CardCategory.category).where(CardCategory.card_id == card.id)
        )
    )
    energy_types = list(
        await session.scalars(
            select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
        )
    )
    fields = [
        group
        for group in (
            _emoji_group(energy_types, ENERGY_EMOJIS),
            _emoji_group(categories, CATEGORY_EMOJIS),
            f"⚡{card.effort_points}" if card.effort_points is not None else "",
        )
        if group
    ]
    return _with_citation_fields(leading, fields)


async def _check_citation_label(session: AsyncSession, check: Check) -> str | None:
    """A live Check keeps the model's own words.

    A closed repeat has to carry its marker, or the link looks exactly like the open one it
    was superseded by.
    """
    marker = await repeat_marker(session, check)
    return f"{_short_citation_title(check.title)}{marker}" if marker else None


async def _citation_label(
    session: AsyncSession, services: Services, item_type: str, item: Any
) -> str | None:
    """Build the fixed, compact label for item types that own their presentation."""
    if item_type == "card":
        return await _card_citation_label(session, item)
    if item_type == "check":
        return await _check_citation_label(session, item)
    if item_type == "tag":
        return f"🏷 {_short_citation_title(item.name)}"
    if item_type == "value":
        return f"💎 {_short_citation_title(item.name)}"
    if item_type == "request":
        matches = await request_cards(session, item.query_sql, services.views)
        return _with_citation_fields(f"💬 {_short_citation_title(item.name)}", [str(len(matches))])
    if item_type == "diary":
        return diary_label(item.entry_date, item.feeling_score)
    return None


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

    Live Cards and saved item types are also named here rather than by the model.  That keeps
    every link compact and gives its metadata directly from the current saved item.
    """
    matches = list(CITATION_PATTERN.finditer(text))
    if not matches:
        return CITATION_MARKUP.sub(lambda match: match[1], text)
    live: dict[tuple[str, int], str | None] = {}
    if services.bot_username:
        for item_type, item_id in {(match[2], int(match[3])) for match in matches}:
            item = await session.get(OPENABLE_MODELS[item_type], item_id)
            if item is None or getattr(item, "archived_at", None) is not None:
                continue
            live[(item_type, item_id)] = await _citation_label(
                session, services, item_type, item
            )

    def build(match: re.Match[str]) -> str:
        label, item_type, item_id = match[1], match[2], int(match[3])
        if (item_type, item_id) not in live:
            return label
        link = f"https://t.me/{services.bot_username}?start={citation_payload(item_type, item_id)}"
        override = live[(item_type, item_id)]
        shown = html.escape(override) if override is not None else label
        return f'<a href="{link}">{shown}</a>'

    # A target that is not an id — a stamp, a date, an invented number — never reached the
    # pass above, and raw Markdown must not stay in a message the chat keeps for good.
    return CITATION_MARKUP.sub(lambda match: match[1], CITATION_PATTERN.sub(build, text))


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
