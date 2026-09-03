"""The Sprint screen, and the Planning screen that stands in for it.

The running Sprint is a list of Actions under what the Sprint committed to, so it is
composed here out of the Card list Cards draws.  Planning carries the Success criteria
and the shape of the plan, and it offers Start only once it has both, because a Sprint
that begins without either is a Sprint nobody can close against anything.  The plan
itself is built one screen further in, in `plan.py`.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ....adapters.kinds import MessageKind
from ....foundation.clock import utcnow
from ....foundation.errors import DomainError
from ....foundation.screens import TextInputFlow
from ....foundation.workspace import Workspace
from ....shell import (
    Services,
    TextInputScreen,
    edit_registered_message,
    menu_row,
    render_text_input,
    required_text,
    send_registered,
    token_button,
    with_notice,
)
from ...cards.api import CardStage, actions_on_stages
from ...cards.model import Card
from ...cards.telegram import stage_list_block
from ...profile.api import capacity_effort_points
from ..model import Sprint
from ..use_cases import set_sprint_success_criteria, sprint_length_days, sprint_metrics

_PROMPT_TTL = timedelta(minutes=30)



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
        block, rows = await stage_list_block(
            session,
            services,
            CardStage.SPRINT,
            title=f"Sprint {sprint.number}",
            page=page,
            action="sprint_page",
            header=(
                f"{sprint.planned_start_date} – {sprint.planned_end_date}\n"
                f"Success criteria: {html.escape(sprint.success_criteria)}\n"
                f"Committed {metrics['committed']} · Added {metrics['added']} · "
                f"Done {metrics['completed']} · Cancelled {metrics['cancelled']}"
            ),
        )
        # On the last day ending the Sprint is not early, and the button says so.
        local_today = utcnow().astimezone(ZoneInfo(workspace.timezone)).date()
        finishing = "⏹ Finish Sprint" if local_today >= sprint.planned_end_date else "⏹ Finish early"
        rows.append([await token_button(session, services.owner_id, finishing, "sprint_finish")])
        rows.append(menu_row())
        await session.commit()
    text = with_notice(block, notice)
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


async def _apply_success_criteria(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del services, state
    await set_sprint_success_criteria(session, value)


async def _render_sprint(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del value
    await render_sprint(
        message, services, replace_message_id=int(state["text_input"]["message_id"])
    )


TEXT_INPUT = TextInputFlow(
    name="sprint",
    validator=lambda _state: required_text("Success criteria"),
    apply=_apply_success_criteria,
    render=_render_sprint,
)
