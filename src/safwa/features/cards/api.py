"""What another feature may ask of Cards.

A stage is a Card's own word, so the enum comes through this door with the two questions
Planning asks of it. Nothing here imports the Card use cases: Planning reads through this
door and Cards writes through Planning's, which is what keeps the two acyclic.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...enums import CardKind
from .model import TERMINAL_STAGES as TERMINAL_STAGES
from .model import Card as Card
from .model import CardStage as CardStage
from .model import is_closed_repeat as is_closed_repeat

PLANNED_STAGES = (CardStage.SPRINT, CardStage.TODAY)


async def actions_on_stages(session: AsyncSession, *stages: CardStage) -> list[Card]:
    """The Actions standing on these stages. An archived one never stands on a live stage."""
    return list(
        await session.scalars(
            select(Card).where(
                Card.kind == CardKind.ACTION.value,
                Card.effective_stage.in_([stage.value for stage in stages]),
                Card.archived_at.is_(None),
            )
        )
    )


async def planned_actions(session: AsyncSession) -> list[Card]:
    """The Actions a Sprint would start with, or is running with."""
    return await actions_on_stages(session, *PLANNED_STAGES)


async def action_titles(session: AsyncSession, card_ids: Iterable[int]) -> list[str]:
    """What the named Cards are called, for a feature that has to name them."""
    wanted = list(card_ids)
    if not wanted:
        return []
    return list(await session.scalars(select(Card.title).where(Card.id.in_(wanted))))


async def live_repeat_instance_id(session: AsyncSession, card: Card) -> int | None:
    """The open Card in this repeat series, or None when the series has ended."""
    return await session.scalar(
        select(Card.id)
        .where(
            Card.repeat_series_id == (card.repeat_series_id or card.id),
            Card.effective_stage.notin_([stage.value for stage in TERMINAL_STAGES]),
        )
        .order_by(Card.id.desc())
        .limit(1)
    )
