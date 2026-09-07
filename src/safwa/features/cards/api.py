"""What another feature may ask of Cards.

A stage is a Card's own word, so the enum comes through this door with the two questions
Planning asks of it, and the rule that a query has to come back with Card ids. Nothing here
imports the Card use cases: Planning reads through this door and Cards writes through
Planning's, which is what keeps the two acyclic.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import UnsafeQueryError, validated_read
from tg_agent_shell.foundation.errors import DomainError

from ...foundation.marks import title_marks
from .model import TERMINAL_STAGES as TERMINAL_STAGES
from .model import Card as Card
from .model import CardKind
from .model import CardStage as CardStage
from .views import AI_CARDS

PLANNED_STAGES = (CardStage.SPRINT, CardStage.TODAY)


class CardQueryError(ValueError):
    """A read that has to come back with Card ids, and does not."""


async def normalize_card_query(
    session: AsyncSession, raw: str, views: Collection[str]
) -> str:
    """Validate a read query that has to come back with Card ids.

    Two callers, and neither owns it: a saved Request's SQL, and the `parent_query` a Card
    proposal may resolve its parent with. Both need the shared read validator plus the two
    rules that make the result usable as Card ids.

    Both rules are asked of the statement itself rather than of its text: the validator says
    which views it reads, and SQLite says what its outermost SELECT comes back with. A
    query whose `ai_cards` sits in a string literal, and one whose `id` belongs to an inner
    SELECT nobody selects from, both read as correct in the text and neither is.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise CardQueryError("A Card query is required")
    try:
        statement, sources = validated_read(raw, views)
    except UnsafeQueryError as error:
        raise CardQueryError(str(error)) from error
    if AI_CARDS.name not in sources:
        raise CardQueryError("The query must read ai_cards and return Card ids")
    if "id" not in await _result_columns(session, statement):
        raise CardQueryError("The query must return a column named id")
    return statement


async def _result_columns(session: AsyncSession, statement: str) -> tuple[str, ...]:
    """What a read comes back with, compiled but never run.

    `LIMIT 0` is what makes this a compilation: a query that matches nothing has the same
    columns as one that matches everything, so an empty workspace refuses nothing.
    """
    try:
        result = await (await session.connection()).exec_driver_sql(
            f"SELECT * FROM ({statement}) LIMIT 0"
        )
    except SQLAlchemyError as error:
        raise CardQueryError(f"The query does not run: {getattr(error, 'orig', error)}") from error
    return tuple(result.keys())


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

