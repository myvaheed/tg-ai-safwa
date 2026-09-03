"""Every screen that shows several Cards at once: a stage dashboard, and one Card's children."""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ....adapters.kinds import MessageKind
from ....foundation.errors import DomainError
from ....foundation.marks import title_marks
from ....foundation.workspace import Workspace
from ....shell import (
    Page,
    Services,
    menu_row,
    paging_row,
    send_registered,
    token_button,
    with_notice,
)
from ..api import actions_on_stages
from ..model import LIVE_STAGE_PRECEDENCE, Card, CardStage
from ..use_cases import card_children
from .presentation import kind_label, paginate_cards


@dataclass(frozen=True, slots=True)
class QuickMove:
    """One tap from a stage list: where it sends an Action, and how the row reads."""

    target: CardStage
    emoji: str
    header: str


# One tap moves an Action one step along the ladder, and the same list is every stage
# dashboard there is: the Backlog, the Sprint and Today differ by this line alone.
STAGE_QUICK_MOVE = {
    CardStage.BACKLOG: QuickMove(
        CardStage.SPRINT, "🏃", "🏃 moves an Action into the Sprint."
    ),
    CardStage.SPRINT: QuickMove(
        CardStage.TODAY, "☀️", "☀️ moves an Action into Today."
    ),
    CardStage.TODAY: QuickMove(
        CardStage.SPRINT, "🏃", "🏃 moves an Action back to the Sprint."
    ),
}


def moves_up(stage: CardStage, move: QuickMove) -> bool:
    """The button sits on the side the move goes: up the ladder right, back down left."""
    return LIVE_STAGE_PRECEDENCE[move.target] > LIVE_STAGE_PRECEDENCE[stage]


async def card_list_rows(
    session: AsyncSession,
    services: Services,
    cards: list[Card],
    *,
    page: int,
    back: dict[str, Any],
    stage: CardStage | None = None,
    prefix: Callable[[Card], str] | None = None,
) -> tuple[Page, list[str], list[list[InlineKeyboardButton]]]:
    """One Card list: the page, its plain-text lines, and one button row per Card.

    `stage` is the list's own stage, which is what decides the one-tap move: where it
    sends an Action, and which side of the row it sits on.
    """
    move = STAGE_QUICK_MOVE.get(stage) if stage is not None else None
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
                label[: 40 if move else 60],
                "card_view",
                {"id": card.id, "back": back},
            )
        ]
        if move is not None:
            button = await token_button(
                session,
                services.owner_id,
                move.emoji,
                "card_quick_move",
                {"id": card.id, "stage": move.target.value, "back": back},
            )
            row.insert(1 if moves_up(stage, move) else 0, button)
        rows.append(row)
    return current, descriptions, rows


def card_list_text(title: str, page: Page, descriptions: list[str], *, header: str = "") -> str:
    body = "\n".join(html.escape(description) for description in descriptions)
    return (
        f"<b>{html.escape(title)}</b> · {page.label}\n"
        + (f"{header}\n" if header else "")
        + (body or "Nothing here yet.")
    )


async def stage_list_block(
    session: AsyncSession,
    services: Services,
    stage: CardStage,
    *,
    title: str,
    page: int,
    action: str,
    payload: dict[str, Any] | None = None,
    header: str = "",
) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """One stage's Actions as a block: its text and its rows, with nothing sent.

    Every stage list is this block.  What a screen puts above it is `header` — the
    Sprint's dates and metrics, nothing on a plain list — and what it puts under it is
    its own rows.  `action` is where paging and `back` come back to, so a screen redraws
    itself rather than another one showing the same stage.
    """
    payload = payload or {}
    cards = await actions_on_stages(session, stage)
    current, descriptions, rows = await card_list_rows(
        session,
        services,
        cards,
        page=page,
        back={"action": action, **payload},
        stage=stage,
    )
    rows.extend(await paging_row(session, services.owner_id, current, action, payload))
    tap = STAGE_QUICK_MOVE[stage].header
    return (
        card_list_text(
            title, current, descriptions, header=f"{header}\n{tap}" if header else tap
        ),
        rows,
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
        text, rows = await stage_list_block(
            session,
            services,
            stage,
            title=title,
            page=page,
            action="dashboard_page",
            payload={"stage": stage.value, "title": title},
        )
        rows.append(menu_row())
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(text, notice),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def command_backlog(message: Message, services: Services) -> None:
    await render_dashboard(message, services, CardStage.BACKLOG, title="Backlog")


async def command_today(message: Message, services: Services) -> None:
    """Today is the Sprint's own stage, so it is a screen only while one runs."""
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
    if workspace is None or not workspace.active_sprint_id:
        await send_registered(
            message,
            services,
            "Today opens once a Sprint is running. Plan the next Sprint first.",
            kind=MessageKind.ERROR,
            markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
        )
        return
    await render_dashboard(message, services, CardStage.TODAY, title="Today")


async def render_children(
    message: Message,
    services: Services,
    parent_id: int,
    *,
    page: int = 0,
    back: dict[str, Any] | None = None,
) -> None:
    back = back or {}
    async with services.sessions() as session:
        parent = await session.get(Card, parent_id)
        if parent is None:
            raise DomainError("Parent Card does not exist")
        children = await card_children(session, parent.id)
        current = paginate_cards(children, page)
        child_back = {
            "action": "card_children",
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
