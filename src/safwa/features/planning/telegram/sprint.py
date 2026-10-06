"""The Sprint screen, and the Planning screen that stands in for it.

The running Sprint shows one selected list without separate Action buttons: each title is a
link that opens its Card in place of the screen, and the Card's Back returns to that list and
page. Planning carries
the Success criteria and the shape of the plan, and it offers Start only once it has both, because a Sprint
that begins without either is a Sprint nobody can close against anything.  The plan
itself is built one screen further in, in `plan.py`.
"""

from __future__ import annotations

import html
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
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
    start_link,
    token_button,
    with_notice,
)
from tg_agent_shell.telegram.contributions import StartLink, TextInputFlow

from ....foundation.workspace import Workspace
from ...cards.api import CardStage, actions_on_stages, effort_label, list_order
from ...cards.model import Card
from ...cards.telegram import render_card
from ...profile.api import effort_tracking_on
from ..api import (
    SPRINT_LENGTH_MAX_DAYS,
    SPRINT_LENGTH_MIN_DAYS,
    PlanLoad,
    capacity_effort_points,
    plan_load,
    sprint_counts,
    sprint_day,
    sprint_length_days,
    sprint_metrics,
    today_actions,
)
from ..model import Sprint, SprintCommitment
from ..use_cases import set_sprint_capacity, set_sprint_length, set_sprint_success_criteria

_PROMPT_TTL = timedelta(minutes=30)

# The lists the running Sprint selects between. A tap on a title names its Card, its list and
# its page, so the Card's Back draws that list and page again.
SPRINT_VIEWS = ("today", "remaining", "done", "blocked")
_OPEN_PAYLOAD = re.compile(rf"^ss-(\d{{1,9}})-({'|'.join(SPRINT_VIEWS)})-(\d{{1,4}})$")


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
            title = start_link(
                services.bot_username,
                html.escape(card.title),
                f"ss-{card.id}-{view}-{shown.index}",
            )
            line = f"{mark} {title}"
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


def claims_sprint_payload(payload: str) -> bool:
    """Whether this deep link is a tap on an Action in one of the running Sprint's lists."""
    return _OPEN_PAYLOAD.fullmatch(payload) is not None


async def handle_sprint_start(message: Message, services: Services, payload: str) -> None:
    """Open the tapped Action in place of the Sprint screen, the one screen left in the chat."""
    card_id, view, page = _OPEN_PAYLOAD.fullmatch(payload).groups()
    screens = await services.chat.notes.outgoing(
        message.chat.id, kinds={MessageKind.DASHBOARD.value}
    )
    await render_card(
        message,
        services,
        int(card_id),
        replace_message_id=screens[0].message_id if screens else None,
        back={"action": "sprint_page", "view": view, "page": int(page)},
    )


SPRINT_LINK = StartLink(claims=claims_sprint_payload, open=handle_sprint_start)


@dataclass(frozen=True, slots=True)
class PlanningField:
    """One value of the next Sprint, edited through a button and a focused prompt."""

    title: str
    instruction: str
    parse: Callable[[str], Any]
    show: Callable[[Workspace], str]
    write: Callable[[AsyncSession, Any], Awaitable[object]]


def _parse_length(raw: str) -> int:
    if not raw.isdigit() or not SPRINT_LENGTH_MIN_DAYS <= int(raw) <= SPRINT_LENGTH_MAX_DAYS:
        raise ValueError(
            f"Send a whole number between {SPRINT_LENGTH_MIN_DAYS} and {SPRINT_LENGTH_MAX_DAYS}."
        )
    return int(raw)


def _parse_capacity(raw: str) -> float | None:
    if raw.lower() == "off":
        return None
    try:
        points = float(raw.replace(",", "."))
    except ValueError:
        raise ValueError("Send a positive number of effort points, or off.") from None
    if points <= 0:
        raise ValueError("Send a positive number of effort points, or off.")
    return points


def capacity_label(points: float | None) -> str:
    return f"{effort_label(points)} EP" if points is not None else "off"


PLANNING_FIELDS: dict[str, PlanningField] = {
    "success_criteria": PlanningField(
        title="Sprint Success criteria",
        instruction="Send what this Sprint must achieve. It is what the Sprint is judged against.",
        parse=required_text("Success criteria"),
        show=lambda workspace: workspace.sprint_success_criteria.strip(),
        write=set_sprint_success_criteria,
    ),
    "length_days": PlanningField(
        title="Sprint length",
        instruction=(
            f"Send a number of days between {SPRINT_LENGTH_MIN_DAYS} and "
            f"{SPRINT_LENGTH_MAX_DAYS}. It applies to the next Sprint you start."
        ),
        parse=_parse_length,
        show=lambda workspace: f"{workspace.sprint_length_days} days",
        write=set_sprint_length,
    ),
    "capacity_effort_points": PlanningField(
        title="Sprint capacity",
        instruction="Send the effort points the next Sprint holds, or off for no capacity.",
        parse=_parse_capacity,
        show=lambda workspace: capacity_label(workspace.sprint_capacity_effort_points),
        write=set_sprint_capacity,
    ),
}


async def render_sprint_field_prompt(
    message: Message, services: Services, field: str, *, notice: str | None = None
) -> None:
    """Ask for one value of the next Sprint."""
    edited = PLANNING_FIELDS[field]
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        if workspace is None or workspace.active_sprint_id:
            raise DomainError("A Sprint is already running")
        current = edited.show(workspace)
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title=edited.title,
            current_value=current,
            instruction=edited.instruction,
            back_action="sprint_back",
            back_payload={},
        ),
        state={"flow": "sprint", "field": field},
        notice=notice,
    )


async def _render_planning(
    message: Message,
    services: Services,
    *,
    notice: str | None = None,
    replace_message_id: int | None = None,
) -> None:
    """What the next Sprint would be, and the things that can change it."""
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        capacity = await capacity_effort_points(session)
        effort_tracking = await effort_tracking_on(session)
        length = await sprint_length_days(session)
        planned = await actions_on_stages(session, CardStage.SPRINT, CardStage.TODAY)
        load = await plan_load(session, planned)
        criteria = (workspace.sprint_success_criteria or "").strip() if workspace else ""
        first = utcnow().astimezone(ZoneInfo(workspace.timezone)).date()
        last = first + timedelta(days=length - 1)
        settings = [
            await token_button(
                session, services.owner_id, f"🏁 Length: {length} days",
                "sprint_edit", {"field": "length_days"},
            )
        ]
        if effort_tracking:
            settings.append(
                await token_button(
                    session, services.owner_id, f"⚖️ Capacity: {capacity_label(capacity)}",
                    "sprint_edit", {"field": "capacity_effort_points"},
                )
            )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    "✏️ Edit Success criteria" if criteria else "🎯 Set Success criteria",
                    "sprint_edit",
                    {"field": "success_criteria"},
                )
            ],
            settings,
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
        f"Length: {length} days, {first:%d.%m} – {last:%d.%m}\n"
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


def _edited_field(state: Mapping[str, Any]) -> PlanningField:
    field = PLANNING_FIELDS.get(str(state.get("field")))
    if field is None:
        raise DomainError("That setting is no longer available.")
    return field


async def _apply_field(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: Any
) -> None:
    del services
    await _edited_field(state).write(session, value)


async def _render_sprint(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del value
    await render_sprint(
        message, services, replace_message_id=int(state["text_input"]["message_id"])
    )


TEXT_INPUT = TextInputFlow(
    name="sprint",
    validator=lambda state: _edited_field(state).parse,
    apply=_apply_field,
    render=_render_sprint,
)
