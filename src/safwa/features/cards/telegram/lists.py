"""Every screen that shows several Cards at once: a stage dashboard, and one Card's children."""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Page,
    Services,
    menu_row,
    paginate,
    paging_row,
    send_registered,
    token_button,
    with_notice,
)

from ...planning.api import plan_load, today_actions
from ...profile.api import effort_tracking_on
from ...schedules.api import appointment_label, workspace_zone
from ..api import actions_on_stages, list_order
from ..hierarchy import card_children
from ..model import Card, CardStage, effort_label
from .presentation import card_title_marks, kind_label, paginate_cards


async def card_list_rows(
    session: AsyncSession,
    services: Services,
    cards: list[Card],
    *,
    page: int,
    back: dict[str, Any],
    prefix: Callable[[Card], str] | None = None,
    counts: dict[int, int | None] | None = None,
) -> tuple[Page, list[str], list[list[InlineKeyboardButton]]]:
    """One Card list: the page, its plain-text lines, and one full-width button per Card."""
    tz = await workspace_zone(session)
    current = paginate(cards, page)
    effort_tracking = await effort_tracking_on(session)
    back = {**back, "page": current.index}
    rows: list[list[InlineKeyboardButton]] = []
    descriptions: list[str] = []
    for card in current.items:
        metadata = [
            kind_label(card.kind),
            card.priority.title(),
        ]
        quantity = counts[card.id] if counts is not None else 1
        if quantity != 1:
            metadata.append(f"× {quantity if quantity is not None else '?'}")
        if effort_tracking:
            effort = card.effort_points * quantity if card.effort_points is not None and quantity is not None else None
            metadata.append(f"{effort_label(effort)} EP")
        if card.scheduled_at is not None:
            label = appointment_label(card.schedule_record.rule, card.scheduled_at, tz, "%d.%m")
            metadata.append(f"⏱ {label}")
        if card.deadline_at is not None:
            metadata.append(f"⏰ {card.deadline_at.astimezone(tz):%d.%m}")
        elif card.schedule:
            metadata.append("Schedule")
        if card.blocked:
            metadata.append("Blocked")
        marks = await card_title_marks(session, card)
        label = f"{prefix(card) if prefix else ''}{card.title}{marks} · {' · '.join(metadata)}"
        descriptions.append(f"• {label}")
        row = [
            await token_button(
                session,
                services.owner_id,
                label[:60],
                "card_view",
                {"id": card.id, "back": back},
            )
        ]
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

    Every plain stage dashboard is this block. What a screen puts above it is `header`,
    and what it puts under it is its own rows. `action` is where paging and `back` come back to, so a screen redraws
    itself rather than another one showing the same stage.
    """
    payload = payload or {}
    # Today is in the order the day cannot move; the other stages in their own.
    cards = (
        await today_actions(session)
        if stage is CardStage.TODAY
        else sorted(await actions_on_stages(session, stage), key=list_order)
    )
    load = None
    if stage in {CardStage.TODAY, CardStage.SPRINT}:
        day = utcnow().astimezone(await workspace_zone(session)).date()
        load = await plan_load(session, cards, **(
            {"start_date": day, "end_date": day} if stage is CardStage.TODAY else {}
        ))
        planned = f"Planned: {'at least ' if load.unknown_schedules else ''}{load.actions} Actions"
        if await effort_tracking_on(session):
            planned += f" · {effort_label(load.effort)} EP"
            if load.unestimated:
                planned += f" · {load.unestimated} Actions have no estimate; EP total is partial"
        if load.unknown_schedules:
            planned += " · Schedule quantities are unknown"
        header = "\n".join(filter(None, [header, planned]))
    current, descriptions, rows = await card_list_rows(
        session,
        services,
        cards,
        page=page,
        back={"action": action, **payload},
        counts=load.counts if load is not None else None,
    )
    rows.extend(await paging_row(session, services.owner_id, current, action, payload))
    return (
        card_list_text(title, current, descriptions, header=header),
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
