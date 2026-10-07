"""What another feature may ask of the workspace context: the Goals it is handed first.

The Home dashboard shows the same Goals in the same order as the context a session reads,
so the order is defined once, here (WS-CONTEXT-008).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cards.model import Card, CardKind, CardStage, Priority
from ..values.model import CardValue, Value

# A Deadline this many local days away, or overdue, puts its Goal first.
PRIORITY_GOAL_DEADLINE_DAYS = 7


async def priority_goals(session: AsyncSession, local_now: datetime) -> list[Card]:
    """Every open root Goal in focus order, with urgent Deadlines before ordinary importance."""
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
    return [goal for goal, _ in ordered]
