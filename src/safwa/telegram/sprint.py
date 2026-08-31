"""The Sprint screens: Today, the running Sprint, and Planning.

Today exists only while a Sprint runs, so both dashboards are here rather than beside the
generic Backlog list.  Planning is what stands in for the Sprint before one starts: it
carries the Success criteria and the shape of the plan, and it offers Start only once it
has both, because a Sprint that begins without either is a Sprint nobody can close against
anything.  The plan itself is built one screen further in, in `plan.py`.
"""

from __future__ import annotations

import html
from datetime import timedelta
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup, Message

from ..domain import DomainError, sprint_length_days, sprint_metrics
from ..enums import MessageKind
from ..features.cards.api import actions_on_stages
from ..features.cards.model import CardStage
from ..features.profile.api import capacity_effort_points
from ..foundation.clock import utcnow
from ..models import Card, Sprint, Workspace
from ..shell import (
    Services,
    TextInputScreen,
    edit_registered_message,
    menu_row,
    paging_row,
    render_text_input,
    send_registered,
    token_button,
    with_notice,
)
from .cards import card_list_rows, card_list_text

_PROMPT_TTL = timedelta(minutes=30)

TODAY_BACK = {"kind": "dashboard", "stage": CardStage.TODAY.value, "title": "Today", "mode": "today"}
SPRINT_BACK = {
    "kind": "dashboard",
    "stage": CardStage.SPRINT.value,
    "title": "Sprint",
    "mode": "sprint",
}


async def render_today(
    message: Message, services: Services, *, page: int = 0, notice: str | None = None
) -> None:
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
        cards = await actions_on_stages(session, CardStage.TODAY)
        current, descriptions, rows = await card_list_rows(
            session,
            services,
            cards,
            page=page,
            back=TODAY_BACK,
            quick_move=CardStage.SPRINT,
        )
        rows.extend(
            await paging_row(
                session, services.owner_id, current, "dashboard_page", dict(TODAY_BACK)
            )
        )
        rows.append(menu_row())
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(
            card_list_text(
                "Today", current, descriptions, header="🏃 moves an Action back to the Sprint."
            ),
            notice,
        ),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def render_sprint(
    message: Message,
    services: Services,
    *,
    page: int = 0,
    notice: str | None = None,
    replace_message_id: int | None = None,
) -> None:
    """The running Sprint with its Actions, or the Planning screen that starts one."""
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        active_sprint_id = workspace.active_sprint_id if workspace else None
    if active_sprint_id is None:
        await _render_planning(
            message, services, notice=notice, replace_message_id=replace_message_id
        )
        return
    async with services.sessions() as session:
        sprint = await session.get(Sprint, active_sprint_id)
        metrics = await sprint_metrics(session, sprint.id)
        cards = await actions_on_stages(session, CardStage.SPRINT)
        current, descriptions, rows = await card_list_rows(
            session,
            services,
            cards,
            page=page,
            back=SPRINT_BACK,
            quick_move=CardStage.TODAY,
        )
        rows.extend(
            await paging_row(
                session, services.owner_id, current, "dashboard_page", dict(SPRINT_BACK)
            )
        )
        # On the last day ending the Sprint is not early, and the button says so.
        local_today = utcnow().astimezone(ZoneInfo(workspace.timezone)).date()
        finishing = "⏹ Finish Sprint" if local_today >= sprint.planned_end_date else "⏹ Finish early"
        rows.append([await token_button(session, services.owner_id, finishing, "sprint_finish")])
        rows.append(menu_row())
        header = (
            f"{sprint.planned_start_date} – {sprint.planned_end_date}\n"
            f"Success criteria: {html.escape(sprint.success_criteria)}\n"
            f"Committed {metrics['committed']} · Added {metrics['added']} · "
            f"Done {metrics['completed']} · Cancelled {metrics['cancelled']}\n"
            "☀️ moves an Action into Today."
        )
        title = f"Sprint {sprint.number}"
        await session.commit()
    text = with_notice(card_list_text(title, current, descriptions, header=header), notice)
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
        )
    else:
        await send_registered(message, services, text, kind=MessageKind.DASHBOARD, markup=markup)


async def render_sprint_criteria_prompt(
    message: Message, services: Services, *, notice: str | None = None
) -> None:
    """Ask what the next Sprint must achieve before showing its plan."""
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        if workspace is None or workspace.active_sprint_id:
            raise DomainError("A Sprint is already running")
        current = workspace.sprint_success_criteria.strip()
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title="Sprint Success criteria",
            current_value=current,
            instruction="Send what this Sprint must achieve. It is what the Sprint is judged against.",
            back_action="sprint_back",
            back_payload={},
        ),
        state={"flow": "sprint"},
        notice=notice,
    )


async def _render_planning(
    message: Message,
    services: Services,
    *,
    notice: str | None = None,
    replace_message_id: int | None = None,
) -> None:
    """What the next Sprint would be, and the three things that can change it."""
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        capacity = await capacity_effort_points(session)
        length = await sprint_length_days(session)
        planned = await actions_on_stages(session, CardStage.SPRINT, CardStage.TODAY)
        criteria = (workspace.sprint_success_criteria or "").strip() if workspace else ""
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    "✏️ Edit Success criteria" if criteria else "🎯 Set Success criteria",
                    "sprint_criteria_prompt",
                )
            ],
            [await token_button(session, services.owner_id, "🗓 Plan", "plan_open")],
        ]
        if planned and criteria:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"▶️ Start {length}-day Sprint",
                        "sprint_start",
                    )
                ]
            )
        rows.append(menu_row())
        await session.commit()
    cost, warning = plan_cost(planned, capacity)
    text = with_notice(
        "<b>Planning</b>\n"
        f"Success criteria: {html.escape(criteria) if criteria else 'not set yet'}\n"
        f"Planned: {cost}" + (f"\n{warning}" if warning else ""),
        notice,
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
        )
    else:
        await send_registered(message, services, text, kind=MessageKind.DASHBOARD, markup=markup)


def plan_cost(planned: list[Card], capacity: int | None) -> tuple[str, str]:
    """What the plan costs and, beside it, the capacity the owner set for a Sprint.

    Written once because both screens that show the plan's total show it against the same
    number, and the warning is advice: nothing about it stops a Sprint from starting.
    """
    effort = sum(card.effort_points or 0 for card in planned)
    line = (
        f"{len(planned)} Actions · {effort} EP · "
        f"capacity {capacity if capacity is not None else '—'} EP"
    )
    above = f"⚠️ Above configured capacity ({capacity} EP)." if capacity and effort > capacity else ""
    return line, above


async def render_sprint_retro(message: Message, services: Services, sprint_id: int) -> None:
    """The Sprint retro screen. It is empty: the retrospective is its own feature."""
    async with services.sessions() as session:
        sprint = await session.get(Sprint, sprint_id)
        if sprint is None:
            raise DomainError("Sprint does not exist")
        rows = [menu_row()]
        text = (
            f"<b>Sprint {sprint.number} retro</b>\n"
            f"{sprint.planned_start_date} – {sprint.planned_end_date}\n"
            f"Success criteria: {html.escape(sprint.success_criteria)}\n\n"
            "There is nothing here yet."
        )
        await session.commit()
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
