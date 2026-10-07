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
from .chat import paging_row, send_registered
from .layout import Page
from .navigation import back_button, place_button
from .place import Place
from .services import Services


def choice_rows(
    options: list[tuple[str, Any]],
    selected: set[Any],
    build: Callable[[Any], Place],
) -> list[tuple[str, Place]]:
    """Each option under its label, ticked when it is selected, and where a tap on it goes."""
    return [
        (f"{'✓ ' if value in selected else ''}{label}", build(value)) for label, value in options
    ]


async def choice_screen(
    message: Message,
    services: Services,
    title: str,
    choices: list[tuple[str, Place]],
    *,
    back: Place,
    paging: tuple[Page, Place] | None = None,
) -> None:
    """The choices, then their pages, then the way back to the screen they were opened
    from. `paging` is the page shown and this screen's own Place."""
    heading = title if paging is None or paging[0].count == 1 else f"{title} · {paging[0].label}"
    async with services.sessions() as session:
        rows = [
            [await place_button(session, services.owner_id, text, place)]
            for text, place in choices
        ]
        if paging is not None:
            rows.extend(await paging_row(session, services.owner_id, *paging))
        rows.append([await back_button(session, services.owner_id, back)])
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>{html.escape(heading)}</b>",
        kind=MessageKind.EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
