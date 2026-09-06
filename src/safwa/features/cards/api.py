"""What another feature may ask of Cards.

A stage is a Card's own word, so the enum comes through this door with the two questions
Planning asks of it, and the rule that a query has to come back with Card ids. Nothing here
imports the Card use cases: Planning reads through this door and Cards writes through
Planning's, which is what keeps the two acyclic.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import UnsafeQueryError, validate_read_sql
from tg_agent_shell.foundation.errors import DomainError

from ...foundation.marks import title_marks
from .model import TERMINAL_STAGES as TERMINAL_STAGES
from .model import Card as Card
from .model import CardKind
from .model import CardStage as CardStage

PLANNED_STAGES = (CardStage.SPRINT, CardStage.TODAY)


class CardQueryError(ValueError):
    """A read that has to come back with Card ids, and does not."""


def normalize_card_query(raw: str, views: Collection[str]) -> str:
    """Validate a read query that has to come back with Card ids.

    Two callers, and neither owns it: a saved Request's SQL, and the `parent_query` a Card
    proposal may resolve its parent with. Both need the shared read validator plus the two
    rules that make the result usable as Card ids.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise CardQueryError("A Card query is required")
    try:
        statement = validate_read_sql(raw, views)
    except UnsafeQueryError as error:
        raise CardQueryError(str(error)) from error
    if "ai_cards" not in statement.casefold():
        raise CardQueryError("The query must read ai_cards and return Card ids")
    if not re.search(
        r"\bselect\s+(?:distinct\s+)?(?:[a-z_][a-z0-9_]*\.)?id(?:\s+as\s+id)?\b",
        statement,
        re.IGNORECASE,
    ):
        raise CardQueryError("The query must return a column named id")
    return statement


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


async def card_title(session: AsyncSession, card_id: int) -> str:
    """What one Card is called, for a screen that hangs off it."""
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    return str(card.title)


async def live_card_title(session: AsyncSession, card_id: int) -> str:
    """The same, refused when the Card is archived: an archived one is read, not answered."""
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    return str(card.title)


async def card_labels(session: AsyncSession, card_ids: Iterable[int]) -> list[str]:
    """What the named Cards are called, with the marks their titles carry."""
    wanted = list(card_ids)
    if not wanted:
        return []
    return [
        card.title + await title_marks(session, card)
        for card in await session.scalars(select(Card).where(Card.id.in_(wanted)))
    ]

