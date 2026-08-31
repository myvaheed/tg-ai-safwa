"""Every screen that shows several Cards at once: a stage dashboard, and one Card's children."""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....enums import CardKind, MessageKind
from ....foundation.errors import DomainError
from ....foundation.marks import title_marks
from ....models import Card
from ....shell import (
    Page,
    Services,
    menu_row,
    paging_row,
    send_registered,
    token_button,
    with_notice,
)
from ..model import CardStage
from ..use_cases import card_children
from .presentation import kind_label, paginate_cards

# Which side a one-tap stage move sits on, so a row always reads the same way: leaving
# Today for the Sprint on the left, pulling a Sprint Action into Today on the right.
QUICK_MOVE_BUTTONS = {
    CardStage.SPRINT: ("🏃", True),
    CardStage.TODAY: ("☀️", False),
}


async def card_list_rows(
    session: AsyncSession,
    services: Services,
    cards: list[Card],
    *,
    page: int,
    back: dict[str, Any],
    quick_move: CardStage | None = None,
    prefix: Callable[[Card], str] | None = None,
) -> tuple[Page, list[str], list[list[InlineKeyboardButton]]]:
    """One Card list: the page, its plain-text lines, and one button row per Card."""
    current = paginate_cards(cards, page)
    back = {**back, "page": current.index}
    rows: list[list[InlineKeyboardButton]] = []
    descriptions: list[str] = []
    for card in current.items:
        metadata = [
            kind_label(card.kind),
            card.priority.title(),
            f"{card.effort_points or '—'} EP",
        ]
        if card.hard_time:
            metadata.append("Hard time")
        if card.repeatable:
            metadata.append("Repeat")
        if card.blocked:
            metadata.append("Blocked")
        marks = await title_marks(session, card)
        label = f"{prefix(card) if prefix else ''}{card.title}{marks} · {' · '.join(metadata)}"
        descriptions.append(f"• {label}")
        row = [
            await token_button(
                session,
                services.owner_id,
                label[: 40 if quick_move else 60],
                "card_view",
                {"id": card.id, "back": back},
            )
        ]
        if quick_move is not None:
            emoji, leading = QUICK_MOVE_BUTTONS[quick_move]
            move = await token_button(
                session,
                services.owner_id,
                emoji,
                "card_quick_move",
                {"id": card.id, "stage": quick_move.value, "back": back},
            )
            row.insert(0 if leading else 1, move)
        rows.append(row)
    return current, descriptions, rows


def card_list_text(title: str, page: Page, descriptions: list[str], *, header: str = "") -> str:
    body = "\n".join(html.escape(description) for description in descriptions)
    return (
        f"<b>{html.escape(title)}</b> · {page.label}\n"
        + (f"{header}\n" if header else "")
        + (body or "Nothing here yet.")
    )


async def render_dashboard(
    message: Message,
    services: Services,
    stage: CardStage,
    *,
    title: str,
    page: int = 0,
    notice: str | None = None,
) -> None:
    async with services.sessions() as session:
        cards = list(
            await session.scalars(
                select(Card).where(
                    Card.effective_stage == stage.value,
                    Card.kind == CardKind.ACTION.value,
                    Card.archived_at.is_(None),
                )
            )
        )
        current, descriptions, rows = await card_list_rows(
            session,
            services,
            cards,
            page=page,
            back={"kind": "dashboard", "stage": stage.value, "title": title},
        )
        rows.extend(
            await paging_row(
                session,
                services.owner_id,
                current,
                "dashboard_page",
                {"stage": stage.value, "title": title},
            )
        )
        rows.append(menu_row())
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(card_list_text(title, current, descriptions), notice),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def command_backlog(message: Message, services: Services) -> None:
    await render_dashboard(message, services, CardStage.BACKLOG, title="Backlog")


async def render_children(
    message: Message,
    services: Services,
    parent_id: int,
    *,
    page: int = 0,
    back: dict[str, Any] | None = None,
) -> None:
    back = back or {"kind": "home"}
    async with services.sessions() as session:
        parent = await session.get(Card, parent_id)
        if parent is None:
            raise DomainError("Parent Card does not exist")
        children = await card_children(session, parent.id)
        current = paginate_cards(children, page)
        child_back = {
            "kind": "children",
            "id": parent.id,
            "page": current.index,
            "back": back,
        }
        rows: list[list[InlineKeyboardButton]] = []
        for child in current.items:
            label = f"{kind_label(child.kind)} · {child.title} · {child.effective_stage.title()}"
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        label[:60],
                        "card_view",
                        {"id": child.id, "back": child_back},
                    )
                ]
            )
        rows.extend(
            await paging_row(
                session,
                services.owner_id,
                current,
                "card_children",
                {"id": parent.id, "back": back},
            )
        )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "card_view",
                    {"id": parent.id, "back": back},
                )
            ]
        )
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
