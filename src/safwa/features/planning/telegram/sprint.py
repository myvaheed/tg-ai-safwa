"""The Sprint screen, and the Planning screen that stands in for it.

The running Sprint shows one selected list without separate Action buttons. Planning carries
the Success criteria and the shape of the plan, and it offers Start only once it has both, because a Sprint
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Services,
    TextInputScreen,
    edit_registered_message,
    menu_row,
    paginate,
    paging_row,
    render_text_input,
    required_text,
    send_registered,
    token_button,
    with_notice,
)
from tg_agent_shell.telegram.contributions import TextInputFlow

from ....foundation.workspace import Workspace
from ...cards.api import CardStage, actions_on_stages, effort_label, list_order
from ...cards.model import Card
from ...profile.api import capacity_effort_points, effort_tracking_on
from ..api import PlanLoad, plan_load, sprint_counts, sprint_day, sprint_metrics, today_actions
from ..model import Sprint, SprintCommitment
from ..use_cases import set_sprint_success_criteria, sprint_length_days

_PROMPT_TTL = timedelta(minutes=30)



async def render_sprint(
    message: Message,
    services: Services,
    *,
    page: int = 0,
    view: str = "remaining",
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
        counts = await sprint_counts(session, sprint.id)
        effort_tracking = await effort_tracking_on(session)
        remaining = sorted(await actions_on_stages(session, CardStage.SPRINT), key=list_order)
        today = await today_actions(session)
        local_today = utcnow().astimezone(ZoneInfo(workspace.timezone)).date()
        today_load = await plan_load(session, today, start_date=local_today, end_date=local_today)
        commitments = {item.card_id: item for item in await session.scalars(
            select(SprintCommitment).where(SprintCommitment.sprint_id == sprint.id)
        )}
        done = sorted(
            await session.scalars(
                select(Card).join(SprintCommitment, SprintCommitment.card_id == Card.id).where(
                    SprintCommitment.sprint_id == sprint.id,
                    SprintCommitment.result == CardStage.DONE.value,
                )
            ),
            key=list_order,
        )
        lists = {
            "today": ("Today", today, "No Actions in Today yet."),
            "remaining": (
                "Remaining", remaining,
                "No Actions remain in Sprint. Check Today or Done; the Sprint is still running.",
            ),
            "done": ("Done", done, "No Actions completed in this Sprint yet."),
            "blocked": (
                "Blocked", sorted([card for card in [*remaining, *today] if card.blocked], key=list_order),
                "No blocked Actions in Sprint or Today.",
            ),
        }
        label, cards, empty = lists[view]
        quantities = {
            key: {
                card.id: (today_load.counts[card.id] if key == "today" else
                          1 if key == "done" or card.id not in commitments
                          else commitments[card.id].planned_count)
                for card in items
            }
            for key, (_, items, _) in lists.items()
        }
        list_counts = {
            key: sum(count or 0 for count in values.values())
            for key, values in quantities.items()
        }
        shown = paginate(cards, page)
        descriptions = []
        for card in shown.items:
            mark = "✓" if view == "done" else "⛔" if card.blocked else "•"
            line = f"{mark} {html.escape(card.title)}"
            quantity = quantities[view][card.id]
            if quantity != 1:
                line += f" × {quantity if quantity is not None else '?'}"
            if effort_tracking:
                commitment = commitments.get(card.id)
                estimate = commitment.effort_snapshot if commitment else card.effort_points
                effort = estimate * quantity if estimate is not None and quantity is not None else None
                line += f" · {effort_label(effort)} EP"
            if view == "blocked":
                line += f"\n<i>Reason: {html.escape(card.blocked_description)}</i>"
            descriptions.append(line)
        rows = []
        buttons = []
        for key, (name, _items, _) in lists.items():
            buttons.append(
                await token_button(
                    session, services.owner_id,
                    f"{'✓ ' if key == view else ''}{name} · {list_counts[key]}",
                    "sprint_page", {"view": key},
                )
            )
        rows.extend([buttons[:2], buttons[2:]])
        rows.extend(await paging_row(session, services.owner_id, shown, "sprint_page", {"view": view}))
        # On the last day ending the Sprint is not early, and the button says so.
        day, length = sprint_day(sprint, local_today)
        page_label = f" · {shown.label}" if shown.count > 1 else ""
        totals = (
            f"Taken <b>{effort_label(metrics['committed'] + metrics['added'])} EP</b> · "
            f"Done <b>{effort_label(metrics['completed'])} EP</b>"
            + (f"\n{counts['unestimated']} Actions have no estimate." if counts['unestimated'] else "")
            if effort_tracking else
            f"Taken <b>{counts['committed'] + counts['added']} Actions</b> · "
            f"Done <b>{counts['completed']} Actions</b>"
        )
        if counts['unknown_schedules']:
            totals += "\nSchedule quantities are unknown; planned totals are lower bounds."
        block = (
            f"<b>Sprint {sprint.number}</b>\n"
            f"{sprint.planned_start_date:%d.%m} – {sprint.planned_end_date:%d.%m} · Day {day} of {length}\n\n"
            f"<b>Success criteria:</b> {html.escape(sprint.success_criteria)}\n\n"
            f"{totals}\n\n"
            f"<b>{label} · {list_counts[view]}</b>{page_label}\n"
            + ("\n".join(descriptions) or empty)
        )
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
        effort_tracking = await effort_tracking_on(session)
        length = await sprint_length_days(session)
        planned = await actions_on_stages(session, CardStage.SPRINT, CardStage.TODAY)
        load = await plan_load(session, planned)
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
    cost, warning = plan_cost(load, capacity, effort_tracking=effort_tracking)
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


def plan_cost(
    load: PlanLoad, capacity: float | None, *, effort_tracking: bool = False
) -> tuple[str, str]:
    """What the plan costs and, beside it, the capacity the owner set for a Sprint.

    Written once because both screens that show the plan's total show it against the same
    number, and the warning is advice: nothing about it stops a Sprint from starting.
    """
    if not effort_tracking:
        return f"{'At least ' if load.unknown_schedules else ''}{load.actions} Actions" + (
            " · Schedule quantities are unknown" if load.unknown_schedules else ""
        ), ""
    effort, unestimated = load.effort, load.unestimated
    line = (
        f"{'At least ' if load.unknown_schedules else ''}{load.actions} Actions · {effort_label(effort)} EP · "
        f"capacity {effort_label(capacity)} EP"
    )
    if unestimated:
        line += f" · {unestimated} Actions have no estimate"
    if load.unknown_schedules:
        line += " · Schedule quantities are unknown; EP totals are partial"
    above = (
        f"⚠️ Above configured capacity ({effort_label(capacity)} EP)."
        if capacity and effort > capacity
        else ""
    )
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
