"""The Dashboard: every stage side by side, and the Backlog a page at a time.

The Dashboard is one table, a column per stage in the order work moves through them, each
column the Cards changed last. The Backlog under it is a numbered list. Both show Actions or
Goals, and every title is a link that opens its Card in place of the screen, whose Back
returns to the same list, page and kind.
"""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    back_button,
    paginate,
    paging_row,
    place_button,
    place_link,
    send_registered,
)

from ...profile.api import effort_tracking_on
from ...schedules.api import appointment_label, workspace_zone
from ..api import list_order
from ..model import Card, CardKind, CardStage, Priority, effort_label
from .presentation import STAGE_EMOJIS, card_title_marks, kind_emoji

# How many Cards of each stage the Dashboard shows, and how many a Backlog page lists.
BOARD_ROWS = 10
BACKLOG_PAGE_SIZE = 10

_COLUMNS = (CardStage.BACKLOG, CardStage.SPRINT, CardStage.TODAY, CardStage.DONE)

# What either screen shows. A Goal takes its stage from the work under it, so Goals are
# shown apart from the Actions rather than mixed in with them.
_KINDS = {
    "actions": ("⭐️ Actions", (CardKind.ACTION.value,)),
    "goals": ("🎯 Goals", (CardKind.GOAL.value, CardKind.SUBGOAL.value)),
}


def _on_stage(stage: CardStage, kinds: str) -> tuple:
    return (
        Card.kind.in_(_KINDS[kinds][1]),
        Card.effective_stage == stage.value,
        Card.archived_at.is_(None),
    )


async def _kind_row(
    session: AsyncSession, services: Services, here: Place
) -> list[InlineKeyboardButton]:
    """The two kinds a screen shows, the one on screen ticked; a switch starts on page 1."""
    return [
        await place_button(
            session,
            services.owner_id,
            f"{'✓ ' if key == here.args['kinds'] else ''}{label}",
            here.but(kinds=key, **({"page": 0} if "page" in here.args else {})),
        )
        for key, (label, _) in _KINDS.items()
    ]


async def _title_link(session: AsyncSession, services: Services, here: Place, card: Card) -> str:
    title = card.title + await card_title_marks(session, card)
    return await place_link(
        session,
        services,
        f"{kind_emoji(card.kind)} {html.escape(title)}",
        here.child("card_view", id=card.id),
    )


async def render_board(message: Message, services: Services, *, kinds: str = "actions") -> None:
    """Every stage as a column of the Cards changed last, and the way into the Backlog."""
    here = Place("board", {"kinds": kinds})
    async with services.sessions() as session:
        headings: list[str] = []
        columns: list[list[str]] = []
        for stage in _COLUMNS:
            total = await session.scalar(select(func.count(Card.id)).where(*_on_stage(stage, kinds)))
            cards = await session.scalars(
                select(Card)
                .where(*_on_stage(stage, kinds))
                .order_by(Card.updated_at.desc(), Card.id.desc())
                .limit(BOARD_ROWS)
            )
            headings.append(f"{STAGE_EMOJIS[stage.value]} {stage.value.title()} · {total}")
            columns.append([await _title_link(session, services, here, card) for card in cards])
        rows = [
            await _kind_row(session, services, here),
            [
                await place_button(
                    session,
                    services.owner_id,
                    "📚 Backlog",
                    here.child("backlog_page", kinds=kinds, page=0),
                )
            ],
            [await back_button(session, services.owner_id, None)],
        ]
        await session.commit()
    table = ["<tr>" + "".join(f"<th>{html.escape(heading)}</th>" for heading in headings) + "</tr>"]
    for index in range(max(map(len, columns))):
        cells = (column[index] if index < len(column) else "" for column in columns)
        table.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    await send_registered(
        message,
        services,
        f"<p><b>🗂 Dashboard</b> · {_KINDS[kinds][0]}</p>"
        f"<table bordered striped>{''.join(table)}</table>",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        rich=True,
    )


async def _details(session: AsyncSession, card: Card, *, effort_tracking: bool) -> list[str]:
    """What a Backlog line says after the title: what is unusual about the Card."""
    details: list[str] = []
    if card.priority != Priority.MEDIUM.value:
        details.append(card.priority.title())
    if effort_tracking and card.kind == CardKind.ACTION.value:
        details.append(f"{effort_label(card.effort_points)} EP")
    if card.scheduled_at is not None:
        label = appointment_label(
            card.schedule_record.rule, card.scheduled_at, await workspace_zone(session), "%d.%m"
        )
        details.append(f"⏱ {label}")
    if card.deadline_at is not None:
        details.append(f"⏰ {card.deadline_at.astimezone(await workspace_zone(session)):%d.%m}")
    if card.blocked:
        details.append("🚧 Blocked")
    return details


async def render_backlog(
    message: Message,
    services: Services,
    *,
    kinds: str = "actions",
    page: int = 0,
    back: Place | None = None,
) -> None:
    """The Backlog in its own order, a page at a time. `back` is the Dashboard it was
    opened from."""
    async with services.sessions() as session:
        cards = sorted(
            await session.scalars(select(Card).where(*_on_stage(CardStage.BACKLOG, kinds))),
            key=list_order,
        )
        shown = paginate(cards, page, BACKLOG_PAGE_SIZE)
        here = Place("backlog_page", {"kinds": kinds, "page": shown.index}, back)
        effort_tracking = await effort_tracking_on(session)
        lines = []
        for number, card in enumerate(shown.items, shown.index * BACKLOG_PAGE_SIZE + 1):
            details = await _details(session, card, effort_tracking=effort_tracking)
            title = await _title_link(session, services, here, card)
            lines.append(" · ".join([f"{number}. {title}", *map(html.escape, details)]))
        rows = [
            *await paging_row(session, services.owner_id, shown, here),
            await _kind_row(session, services, here),
            [await back_button(session, services.owner_id, back)],
        ]
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>📚 Backlog</b> · {_KINDS[kinds][0]} · {len(cards)} · {shown.label}\n"
        + ("\n".join(lines) or "Nothing here yet."),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def command_board(message: Message, services: Services) -> None:
    await render_board(message, services)


async def _on_board(context: CallbackContext) -> None:
    await render_board(context.message, context.services, kinds=str(context.payload["kinds"]))


async def _on_backlog(context: CallbackContext) -> None:
    await render_backlog(
        context.message,
        context.services,
        kinds=str(context.payload["kinds"]),
        page=int(context.payload.get("page", 0)),
        back=context.back,
    )


BOARD_ACTIONS: dict[str, CallbackHandler] = {
    "board": _on_board,
    "backlog_page": _on_backlog,
}
