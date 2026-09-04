"""One screen that offers a list of choices, and the rows it is drawn from.

A Card field, a Card relationship and the Values of a Check are all chosen this way, so
the screen belongs to neither feature. What is being chosen, and what a tap does, comes
from the caller as rows.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message

from ..foundation.kinds import MessageKind
from .chat import paging_row, send_registered, token_button
from .layout import Page
from .services import Services


def choice_rows(
    options: list[tuple[str, Any]],
    selected: set[Any],
    build: Callable[[Any], tuple[str, dict[str, Any]]],
) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for label, value in options:
        action, payload = build(value)
        rows.append((f"{'✓ ' if value in selected else ''}{label}", action, payload))
    return rows


async def choice_screen(
    message: Message,
    services: Services,
    title: str,
    choices: list[tuple[str, str, dict[str, Any]]],
    *,
    back: tuple[str, str, dict[str, Any]] | None = None,
    paging: tuple[Page, str, dict[str, Any]] | None = None,
) -> None:
    heading = title if paging is None or paging[0].count == 1 else f"{title} · {paging[0].label}"
    async with services.sessions() as session:
        rows = [
            [await token_button(session, services.owner_id, text, action, payload)]
            for text, action, payload in choices
        ]
        if paging is not None:
            page, page_action, page_payload = paging
            rows.extend(
                await paging_row(session, services.owner_id, page, page_action, page_payload)
            )
        if back:
            rows.append([await token_button(session, services.owner_id, *back)])
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>{html.escape(heading)}</b>",
        kind=MessageKind.EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
