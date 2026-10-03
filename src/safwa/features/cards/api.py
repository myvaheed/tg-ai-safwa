"""What another feature may ask of Cards.

A stage is a Card's own word, so the enum comes through this door with the two questions
Planning asks of it, and the rule that a query has to come back with Card ids. Nothing here
imports the Card use cases: Planning reads through this door and Cards writes through
Planning's, which is what keeps the two acyclic.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import UnsafeQueryError, validated_read
from tg_agent_shell.foundation.errors import DomainError

from ...foundation.marks import title_marks
from .model import TERMINAL_STAGES as TERMINAL_STAGES
from .model import Card as Card
from .model import CardKind, Priority
from .model import CardStage as CardStage
from .model import effort_label as effort_label
from .model import minutes_label as minutes_label
from .views import AI_CARDS

PLANNED_STAGES = (CardStage.SPRINT, CardStage.TODAY)
# How many days ahead an appointment is near enough to belong in Today: today and tomorrow.
SCHEDULE_NOTICE_DAYS = 1


_PRIORITY_ORDER = {Priority.CRITICAL.value: 0, Priority.MEDIUM.value: 1, Priority.LOW.value: 2}


def list_order(card: Card) -> tuple[bool, datetime, int, datetime]:
    """Appointment first and the sooner one before, then priority, then oldest: the
    ordering of every Card list but Today, which is in the order the day cannot move."""
    return (
        card.scheduled_at is None,
        card.scheduled_at or card.created_at,
        _PRIORITY_ORDER[card.priority],
        card.created_at,
    )


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


async def goal_of(session: AsyncSession, action: Card) -> Card | None:
    """The Goal an Action is under, past a Subgoal between them, or None."""
    parent = await session.get(Card, action.parent_id) if action.parent_id else None
    if parent is not None and parent.kind == CardKind.SUBGOAL.value:
        return await session.get(Card, parent.parent_id) if parent.parent_id else None
    return parent


async def finished_actions(session: AsyncSession, limit: int) -> list[Card]:
    """The Actions finished last, newest first, however long ago and archived or not."""
    return list(
        await session.scalars(
            select(Card)
            .where(
                Card.kind == CardKind.ACTION.value,
                Card.effective_stage == CardStage.DONE.value,
                Card.completed_at.is_not(None),
            )
            .order_by(Card.completed_at.desc(), Card.id.desc())
            .limit(limit)
        )
    )


async def open_goal_titles(session: AsyncSession) -> list[str]:
    """What the Goals still to reach are called, oldest first."""
    return list(
        await session.scalars(
            select(Card.title)
            .where(
                Card.kind == CardKind.GOAL.value,
                Card.effective_stage != CardStage.DONE.value,
                Card.archived_at.is_(None),
            )
            .order_by(Card.created_at, Card.id)
        )
    )


async def tracked_between(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple[int, int]:
    """The minutes on the Actions finished in `[start, end)`, and how many carry one."""
    minutes, count = (
        await session.execute(
            select(func.coalesce(func.sum(Card.tracked_mins), 0), func.count(Card.id)).where(
                Card.kind == CardKind.ACTION.value,
                Card.effective_stage == CardStage.DONE.value,
                Card.tracked_mins.is_not(None),
                Card.completed_at >= start,
                Card.completed_at < end,
            )
        )
    ).one()
    return int(minutes), int(count)


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

