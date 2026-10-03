"""The workspace's current state, as one block a session reads before its own steps.

The workspace is what the owner keeps - Cards, Checks, Values, Tags, Requests and Reminders -
so the block that describes it is the workspace's, not the reader's. `AgentSpec.workspace_state`
is the flag that asks for it.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.messages import StateBlocks
from tg_agent_shell.foundation.clock import utcnow

from ...foundation.workspace import Workspace
from ..cards.api import list_order
from ..cards.model import Card, CardStage, Priority, effort_label
from ..planning.api import plan_load, today_actions
from ..planning.model import Sprint
from ..profile.model import UserProfile
from ..tags.model import Tag
from ..values.model import CardValue, Value

# How many critical Cards the workspace state names before the model has to query for more.
CONTEXT_CRITICAL_CARD_LIMIT = 10

def citation(name: str, kind: str, item_id: int) -> str:
    """The one shape an item takes in context, ready for the model to reuse in a reply."""
    return f"[{name}]({kind}:{item_id})"


async def _critical_cards(session: AsyncSession) -> list[Card]:
    """The critical Cards, those carrying an active Value first, each part in list order."""
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
            Card.priority == Priority.CRITICAL.value,
            Card.effective_stage != CardStage.DONE.value,
        )
    )
    ordered = sorted(rows.unique(), key=lambda row: (not row[1], list_order(row[0])))
    return [card for card, _ in ordered[:CONTEXT_CRITICAL_CARD_LIMIT]]


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
    critical = await _critical_cards(session)
    # An empty heading would read the owner's next line as its first item.
    if critical:
        lines.append("Critical Cards:")
        lines.extend(
            f"- {citation(card.title, 'card', card.id)} kind={card.kind} "
            f"stage={card.effective_stage}"
            for card in critical
        )
    today = await today_actions(session)
    local_day = utcnow().astimezone(timezone).date()
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
        clock=f"Current local time: {utcnow().astimezone(timezone):%Y-%m-%d %H:%M} ({timezone})",
    )
