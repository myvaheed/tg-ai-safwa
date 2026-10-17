"""Reminders on answers: the SELECT that finds their items, and what an answer makes of them.

The SELECT is checked when it is written and again every time it runs, as a saved Request's
is: the stored text is all that stands between it and the database. It reads `ai_checks` for
a Check's answer and `ai_cards` for Done, and comes back with the ids of the items it
counts. It runs at every answer, so an item made later counts once the SELECT finds it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import UnsafeQueryError, validated_read

from ..cards.model import Card, CardCheck
from ..checks.api import answer_run, newest_answers
from ..checks.model import Check, CheckOutcome
from ..schedules.rules import most_runs
from ..tags.api import Tag
from ..values.api import value_names
from .api import Condition

# The views a condition's SELECT may read.
CONDITION_VIEWS = frozenset({"ai_checks", "ai_cards"})
# How many of the series found the review screen names before "and N more".
CONDITION_PREVIEW_TITLES = 3


class ConditionError(ValueError):
    """A condition's SELECT that cannot find its items."""


async def item_names(session: AsyncSession) -> str:
    """The Tags and Values as they are stored, for the SELECT to spell: a match on a name is
    exact, and the owner's words need not be."""
    tags = sorted(await session.scalars(select(Tag.name)), key=str.casefold)
    values = list((await value_names(session)).values())
    return f"Tags: {', '.join(tags) or 'none'}\nValues: {', '.join(values) or 'none'}"


def items_view(answer: str) -> str:
    """Done is an Action's answer; every other answer is a Check's."""
    return "ai_cards" if answer == "done" else "ai_checks"


def checked_select(sql: str, answer: str) -> str:
    """The statement to run, or why it cannot find the items of this answer."""
    try:
        statement, read = validated_read(sql, CONDITION_VIEWS)
    except UnsafeQueryError as error:
        raise ConditionError(str(error)) from error
    view = items_view(answer)
    if view not in read:
        raise ConditionError(f"The SELECT must read {view} and return its ids")
    return statement


async def found_ids(session: AsyncSession, condition: Condition) -> list[int]:
    """The ids the condition's SELECT finds now."""
    statement = checked_select(condition.sql, condition.answer)
    try:
        result = await session.execute(text(statement))
    except SQLAlchemyError as error:
        raise ConditionError(f"The SELECT does not run: {getattr(error, 'orig', error)}") from error
    if "id" not in result.keys():
        raise ConditionError("The SELECT must return a column named id")
    try:
        return list(dict.fromkeys(int(row["id"]) for row in result.mappings()))
    except (TypeError, ValueError) as error:
        raise ConditionError("The SELECT's ids must be whole numbers") from error


async def count_now(
    session: AsyncSession, condition: Condition, ids: Sequence[int], item_id: int
) -> str | None:
    """How many answers the condition counts now, as the Cue says it, or None when this
    item's answer is not one it counts or does not bring the count to a multiple."""
    if condition.answer == "done":
        done = list(
            await session.scalars(
                select(Card.id).where(Card.id.in_(ids), Card.completed_at.is_not(None))
            )
        )
        if item_id not in done or len(done) % condition.count:
            return None
        return f"{len(done)} Done now"
    answers = await newest_answers(session, ids)
    if condition.in_row:
        counted = answer_run(answers, condition.answer)
    else:
        counted = [row for row in answers if condition.answer in {"answered", row.outcome}]
    if item_id not in {row.id for row in counted} or len(counted) % condition.count:
        return None
    passed = sum(row.outcome == CheckOutcome.PASSED.value for row in answers)
    row = " in a row" if condition.in_row else ""
    return f"{len(counted)}{row} now; {passed} Passed and {len(answers) - passed} Missed in all"


async def most_answers(
    session: AsyncSession, condition: Condition, ids: Sequence[int]
) -> int | None:
    """The most answers the items can ever have when they are one series: 1 when it does not
    repeat, or its Schedule's total. None for more than one series, or one without end.

    A Check runs as its own Schedule does, or else as the Action it hangs on.
    """
    model: Any = Card if condition.answer == "done" else Check
    series = func.coalesce(Card.repeat_series_id if model is Card else Check.series_id, model.id)
    roots = set(await session.scalars(select(series).where(model.id.in_(ids))))
    if len(roots) != 1:
        return None
    newest = await session.scalar(
        select(model).where(series.in_(roots)).order_by(model.id.desc()).limit(1)
    )
    definition = newest.schedule_record
    if model is Check and definition is None:
        card = await session.scalar(
            select(Card)
            .join(CardCheck, CardCheck.card_id == Card.id)
            .where(CardCheck.check_id == newest.id)
        )
        definition = card.schedule_record if card is not None else None
    return most_runs(definition.rule) if definition is not None and definition.rule else 1


async def finds_now(session: AsyncSession, condition: Condition) -> str:
    """The Reminder screen's line: what the SELECT finds now, or why it counts nothing."""
    try:
        ids = await found_ids(session, condition)
    except ConditionError as error:
        return f"⚠️ It counts nothing: {error}. Ask Safwa to set it again."
    return f"🔎 Finds now: {await finds_text(session, condition, ids)}"


async def finds_text(session: AsyncSession, condition: Condition, ids: Sequence[int]) -> str:
    """What the SELECT finds now: the newest title of each series, up to
    CONDITION_PREVIEW_TITLES of them, and how many more."""
    model: Any = Card if condition.answer == "done" else Check
    series = func.coalesce(Card.repeat_series_id if model is Card else Check.series_id, model.id)
    named: dict[int, str] = {}
    for root, title in await session.execute(
        select(series, model.title).where(model.id.in_(ids)).order_by(model.id.desc())
    ):
        named.setdefault(root, title)
    if not named:
        return "Nothing yet."
    titles = list(named.values())
    shown = ", ".join(f"«{title}»" for title in titles[:CONDITION_PREVIEW_TITLES])
    more = len(titles) - CONDITION_PREVIEW_TITLES
    return shown + (f" and {more} more" if more > 0 else "") + "."
