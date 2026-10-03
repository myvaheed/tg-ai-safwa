"""The workspace's current state, as one block a session reads before its own steps.

The workspace is what the owner keeps - Cards, Checks, Values, Tags, Requests and Reminders -
so the block that describes it is the workspace's, not the reader's. `AgentSpec.workspace_state`
is the flag that asks for it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.messages import StateBlocks
from tg_agent_shell.foundation.clock import utcnow

from ...foundation.workspace import Workspace
from ..cards.model import Card, CardKind, CardStage, Priority, effort_label
from ..planning.api import plan_load, today_actions
from ..planning.model import Sprint
from ..profile.model import UserProfile
from ..tags.model import Tag
from ..values.model import CardValue, Value

CONTEXT_PRIORITY_GOAL_LIMIT = 10
PRIORITY_GOAL_DEADLINE_DAYS = 7

def citation(name: str, kind: str, item_id: int) -> str:
    """The one shape an item takes in context, ready for the model to reuse in a reply."""
    return f"[{name}]({kind}:{item_id})"


async def _priority_goals(session: AsyncSession, local_now: datetime) -> list[Card]:
    """Open Goals in focus order, with urgent Deadlines before ordinary importance."""
    linked_active_value = (
        select(CardValue.card_id)
        .join(Value, Value.id == CardValue.value_id)
        .where(
            CardValue.card_id == Card.id,
            Value.active.is_(True),
        )
        .exists()
    )
    rows = await session.execute(
        select(Card, linked_active_value).where(
            Card.kind == CardKind.GOAL.value,
            Card.parent_id.is_(None),
            Card.effective_stage != CardStage.DONE.value,
            Card.archived_at.is_(None),
        )
    )
    near = local_now.date() + timedelta(days=PRIORITY_GOAL_DEADLINE_DAYS)
    ranks = list(Priority)

    def focus_order(row):
        goal, active_value = row
        deadline = goal.deadline_at
        urgent = deadline is not None and deadline.astimezone(local_now.tzinfo).date() <= near
        return (
            not urgent,
            ranks.index(Priority(goal.priority)),
            not active_value,
            goal.effective_stage not in (CardStage.SPRINT.value, CardStage.TODAY.value),
            deadline is None,
            deadline or local_now,
            goal.created_at,
            goal.id,
        )

    ordered = sorted(rows.unique(), key=focus_order)
    return [goal for goal, _ in ordered[:CONTEXT_PRIORITY_GOAL_LIMIT]]


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
    lines = [
        f"Workspace mode: {workspace.mode if workspace else 'planning'}",
        f"About me: {(profile.about_me if profile else '').strip()}",
        f"Advisor instructions: {(profile.advisor_instructions if profile else '').strip()}",
        f"Effort Points: {'on' if profile and profile.effort_tracking else 'off'}",
        "Active Values: "
        + ", ".join(citation(value.name, "value", value.id) for value in active_values),
        "Available Tags: " + ", ".join(citation(tag.name, "tag", tag.id) for tag in tags),
    ]
    if sprint is not None:
        lines.extend(
            [
                f"Sprint {sprint.number}: {sprint.planned_start_date} – {sprint.planned_end_date}",
                f"Success criteria: {sprint.success_criteria.strip()}",
            ]
        )
    else:
        lines.append(
            "No Sprint is running; the workspace is in Planning. "
            + (
                f"Draft Success criteria for the next one: "
                f"{workspace.sprint_success_criteria.strip()}"
                if workspace and workspace.sprint_success_criteria.strip()
                else "No Success criteria have been written yet."
            )
        )
    goals = await _priority_goals(session, local_now)
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
    local_day = local_now.date()
    load = await plan_load(session, today, start_date=local_day, end_date=local_day)
    lines.append("Today Actions:")
    lines.append(f"Planned executions remaining today: {load.actions}" + (
        " (lower bound; Schedule quantities are unknown)" if load.unknown_schedules else ""
    ))
    if profile and profile.effort_tracking:
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
        + (f" effort={effort_label(card.effort_points)}" if profile and profile.effort_tracking else "")
        + (
            f" schedule_at={card.scheduled_at.astimezone(timezone):%d.%m %H:%M}"
            if card.scheduled_at
            else ""
        )
        for card in today
    )
    return StateBlocks(
        state="\n".join(lines),
        clock=f"Current local time: {local_now:%Y-%m-%d %H:%M} ({timezone})",
    )
