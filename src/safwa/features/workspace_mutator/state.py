"""The workspace's current state, as one block a session reads before its own steps.

The workspace is what the owner keeps - Cards, Checks, Values, Tags, Requests and Reminders -
so the block that describes it is the workspace's, not the reader's. `AgentSpec.workspace_state`
is the flag that asks for it.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.messages import StateBlocks

from ...foundation.workspace import Workspace
from ..cards.model import Card, CardKind, CardStage, Priority, effort_label
from ..planning.model import Sprint
from ..profile.model import UserProfile
from ..tags.model import Tag
from ..values.model import CardValue, Value

# How many critical Cards the workspace state names before the model has to query for more.
CONTEXT_CRITICAL_CARD_LIMIT = 10

# The column stores the name, so ordering by it is alphabetical - low before medium. The
# enum's own order is the priority order, which is what a reader is meant to read down.
PRIORITY_RANK = case(
    {priority.value: rank for rank, priority in enumerate(Priority)}, value=Card.priority
)


def citation(name: str, kind: str, item_id: int) -> str:
    """The one shape an item takes in context, ready for the model to reuse in a reply."""
    return f"[{name}]({kind}:{item_id})"


async def _critical_cards(session: AsyncSession) -> list[Card]:
    """The critical Cards, those carrying an active Value first."""
    linked_active_value = (
        select(CardValue.card_id)
        .join(Value, Value.id == CardValue.value_id)
        .where(
            CardValue.card_id == Card.id,
            Value.active.is_(True),
        )
        .exists()
    )
    return list(
        await session.scalars(
            select(Card)
            .where(
                Card.priority == Priority.CRITICAL.value,
                Card.effective_stage.notin_(
                    [CardStage.DONE.value]
                ),
            )
            .order_by(linked_active_value.desc(), Card.hard_time.desc(), Card.created_at)
            .limit(CONTEXT_CRITICAL_CARD_LIMIT)
        )
    )


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
    lines.append("Critical Cards:")
    lines.extend(
        f"- {citation(card.title, 'card', card.id)} kind={card.kind} stage={card.effective_stage}"
        for card in critical
    )
    if sprint is not None:
        today = list(
            await session.scalars(
                select(Card)
                .where(
                    Card.effective_stage == CardStage.TODAY.value,
                    Card.kind == CardKind.ACTION.value,
                )
                .order_by(Card.hard_time.desc(), PRIORITY_RANK, Card.created_at)
            )
        )
        lines.append("Today Actions:")
        lines.extend(
            f"- {citation(card.title, 'card', card.id)} "
            f"effort={effort_label(card.effort_points)}"
            for card in today
        )
    return StateBlocks(
        state="\n".join(lines),
        clock=f"Current local time: {datetime.now(timezone):%Y-%m-%d %H:%M} ({timezone})",
    )
