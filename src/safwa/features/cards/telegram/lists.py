"""One Card's children, the one list of Cards drawn as buttons.

The stages are drawn side by side on the Dashboard, and the Backlog as a list of links, in
`board.py`.
"""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Place,
    Services,
    back_button,
    paging_row,
    place_button,
    send_registered,
)

from ..hierarchy import card_children
from ..model import Card
from .presentation import item_button_label, paginate_cards


async def render_children(
    message: Message,
    services: Services,
    parent_id: int,
    *,
    page: int = 0,
    back: Place | None = None,
) -> None:
    """One Card's children, a page at a time. `back` is that Card's own screen."""
    async with services.sessions() as session:
        parent = await session.get(Card, parent_id)
        if parent is None:
            raise DomainError("Parent Card does not exist")
        children = await card_children(session, parent.id)
        current = paginate_cards(children, page)
        here = Place("card_children", {"id": parent.id, "page": current.index}, back)
        rows: list[list[InlineKeyboardButton]] = [
            [
                await place_button(
                    session,
                    services.owner_id,
                    await item_button_label(session, child),
                    here.child("card_view", id=child.id),
                )
            ]
            for child in current.items
        ]
        rows.extend(await paging_row(session, services.owner_id, current, here))
        rows.append([await back_button(session, services.owner_id, back)])
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>Children of {html.escape(parent.title)}</b> · "
        f"{len(children)} total · {current.label}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=parent.id,
    )
