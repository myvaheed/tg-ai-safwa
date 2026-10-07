"""The workspace's current state, as one block a session reads before its own steps.

The workspace is what the owner keeps - Cards, Checks, Values, Tags, Requests and Reminders -
so the block that describes it is the workspace's, not the reader's. `AgentSpec.workspace_state`
is the flag that asks for it.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.messages import StateBlocks
from tg_agent_shell.foundation.clock import utcnow

from ...foundation.workspace import Workspace
from ..cards.model import effort_label
from ..planning.api import (
    capacity_effort_points,
    plan_load,
    sprint_day,
    sprint_length_days,
    today_actions,
)
from ..planning.model import Sprint
from ..profile.model import UserProfile
from ..schedules.api import appointment_label
from ..tags.model import Tag
from ..values.model import Value
from .api import priority_goals

CONTEXT_PRIORITY_GOAL_LIMIT = 10

def citation(name: str, kind: str, item_id: int) -> str:
    """The one shape an item takes in context, ready for the model to reuse in a reply."""
    return f"[{name}]({kind}:{item_id})"


async def workspace_context(session: AsyncSession) -> StateBlocks:
    workspace = await session.get(Workspace, 1)
    profile = await session.get(UserProfile, 1)
    active_values = list(
        await session.scalars(
            select(Value)
            .where(Value.active.is_(True))
            .order_by(Value.name)
        )
    )
    tags = list(
        await session.scalars(select(Tag).order_by(Tag.name))
    )
    sprint = (
        await session.get(Sprint, workspace.active_sprint_id)
        if workspace and workspace.active_sprint_id
        else None
    )
    timezone = ZoneInfo(workspace.timezone if workspace else "Europe/Istanbul")
    local_now = utcnow().astimezone(timezone)
    local_day = local_now.date()
    effort_on = bool(profile and profile.effort_tracking)
    lines = [
        f"Workspace mode: {workspace.mode if workspace else 'planning'}",
        f"About me: {(profile.about_me if profile else '').strip()}",
        f"Advisor instructions: {(profile.advisor_instructions if profile else '').strip()}",
        f"Effort Points: {'on' if effort_on else 'off'}",
        "Active Values:",
        *(f"- {citation(value.name, 'value', value.id)}: {value.description}" for value in active_values),
        "Available Tags:",
        *(f"- {citation(tag.name, 'tag', tag.id)}: {tag.description}" for tag in tags),
    ]
    if sprint is not None:
        # The day is counted here, not by the model: a small model misreads date arithmetic.
        day, days = sprint_day(sprint, local_day)
        lines.append(
            f"Sprint {sprint.number}: {sprint.planned_start_date} – {sprint.planned_end_date}, "
            f"{days} days. Today is day {day}. Days left after today: {max(days - day, 0)}."
        )
        if effort_on and sprint.capacity_effort_points is not None:
            lines.append(f"Sprint capacity: {effort_label(sprint.capacity_effort_points)} EP.")
        lines.append(f"Success criteria: {sprint.success_criteria.strip()}")
    else:
        length = await sprint_length_days(session)
        lines.append(
            "No Sprint is running; the workspace is in Planning. "
            + (
                f"Draft Success criteria for the next one: "
                f"{workspace.sprint_success_criteria.strip()}"
                if workspace and workspace.sprint_success_criteria.strip()
                else "No Success criteria have been written yet."
            )
        )
        lines.append(
            f"A Sprint started today runs {local_day} – {local_day + timedelta(days=length - 1)}, "
            f"{length} days."
        )
        if (capacity := await capacity_effort_points(session)) is not None:
            lines.append(f"Next Sprint's capacity: {effort_label(capacity)} EP.")
    goals = (await priority_goals(session, local_now))[:CONTEXT_PRIORITY_GOAL_LIMIT]
    # An empty heading would read the owner's next line as its first item.
    if goals:
        lines.append("Priority Goals:")
        lines.extend(
            f"- {citation(goal.title, 'card', goal.id)} priority={goal.priority} "
            f"stage={goal.effective_stage}"
            + (
                f" deadline={goal.deadline_at.astimezone(timezone):%d.%m.%Y %H:%M}"
                if goal.deadline_at else ""
            )
            for goal in goals
        )
    today = await today_actions(session)
    load = await plan_load(session, today, start_date=local_day, end_date=local_day)
    lines.append("Today Actions:")
    lines.append(f"Planned executions remaining today: {load.actions}" + (
        " (lower bound; Schedule quantities are unknown)" if load.unknown_schedules else ""
    ))
    if effort_on:
        lines.append(f"Remaining planned effort today: {effort_label(load.effort)} EP")
        if load.unestimated:
            lines.append(f"Unestimated executions: {load.unestimated}; EP total is partial.")
    lines.extend(
        f"- {citation(card.title, 'card', card.id)}"
        + (
            f" executions={load.counts[card.id] if load.counts[card.id] is not None else '?'}"
            if load.counts[card.id] != 1
            else ""
        )
        + (f" effort={effort_label(card.effort_points)}" if effort_on else "")
        + (
            f" schedule_at={appointment_label(card.schedule_record.rule, card.scheduled_at, timezone, '%d.%m')}"
            if card.scheduled_at
            else ""
        )
        for card in today
    )
    return StateBlocks(
        state="\n".join(lines),
        clock=f"Current local time: {local_now:%Y-%m-%d %H:%M} ({timezone})",
    )
