"""The open root Goals eligible for the workspace's Priority Goals context."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cards.model import Card, CardKind, CardParent, CardStage


async def priority_goals(session: AsyncSession) -> list[Card]:
    """Every open root Goal; its destination chooses the order and cap."""
    return list(
        await session.scalars(
            select(Card).where(
                Card.kind == CardKind.GOAL.value,
                ~select(CardParent.card_id).where(CardParent.card_id == Card.id).exists(),
                Card.effective_stage != CardStage.DONE.value,
                Card.archived_at.is_(None),
            )
        )
    )
