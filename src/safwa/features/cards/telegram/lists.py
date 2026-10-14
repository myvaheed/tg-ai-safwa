"""Every list of Cards a question returns — a Request, a Goal's children, the Cards carrying a
Value or a Tag — drawn as buttons, one shape for all of them.

The stages are drawn side by side on the Dashboard, and the Backlog as a list of links, in
`board.py`.
"""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Place,
    Services,
    back_button,
    place_button,
    place_link,
    send_registered,
)

from ...profile.api import effort_tracking_on
from ..api import list_order
from ..hierarchy import card_children, cards_progress
from ..model import Card
from .presentation import item_button_label, progress_summary, result_order

# How many Cards one result lists before it only counts the rest.
RESULT_LIMIT = 25


async def card_results(
    session: AsyncSession,
    services: Services,
    cards: list[Card],
    here: Place,
    back: Place | None,
) -> tuple[str, InlineKeyboardMarkup]:
    """The count line and the buttons of a result: its first `RESULT_LIMIT` Cards in
    `result_order`, each opening its Card, then ↻ Refresh and the way back.

    `cards` comes in the order that breaks `result_order`'s ties.
    """
    ordered = await result_order(session, cards)
    rows = [
        [
            await place_button(
                session,
                services.owner_id,
                await item_button_label(session, card),
                here.child("card_view", id=card.id),
            )
        ]
        for card in ordered[:RESULT_LIMIT]
    ]
    rows.append([await place_button(session, services.owner_id, "↻ Refresh", here)])
    rows.append([await back_button(session, services.owner_id, back)])
    count = f"{len(cards)} matching card{'s' if len(cards) != 1 else ''}"
    if len(cards) > RESULT_LIMIT:
        count += f" (showing first {RESULT_LIMIT})"
    return count, InlineKeyboardMarkup(inline_keyboard=rows)


async def cards_link(
    session: AsyncSession, services: Services, cards: list[Card], place: Place
) -> str:
    """`cards` summed up as a Goal's children are, as a link to them as a result; — for none."""
    summary = progress_summary(
        await cards_progress(session, cards), effort_tracking=await effort_tracking_on(session)
    )
    return await place_link(session, services, html.escape(summary), place) if summary else "—"


async def render_children(
    message: Message,
    services: Services,
    parent_id: int,
    *,
    back: Place | None = None,
) -> None:
    """One Card's children as a result. `back` is that Card's own screen."""
    here = Place("card_children", {"id": parent_id}, back)
    async with services.sessions() as session:
        parent = await session.get(Card, parent_id)
        if parent is None:
            raise DomainError("Parent Card does not exist")
        children = sorted(await card_children(session, parent.id), key=list_order)
        count, markup = await card_results(session, services, children, here, back)
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>Children of {html.escape(parent.title)}</b>\n\n{count}",
        kind=MessageKind.DASHBOARD,
        markup=markup,
        related_id=parent.id,
    )
