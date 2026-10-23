"""Reminders on answers: the SELECT that finds their items, and what an answer makes of them.

The SELECT is checked when it is written and again every time it runs, as a saved Request's
is: the stored text is all that stands between it and the database. It reads `ai_checks` for
Checks and `ai_cards` for Actions, and comes back with the ids of the items it finds. It runs
at every answer, so an item made later counts once the SELECT finds it.

What the items are is the items' to say, never the answer's: yes is a Check's Passed and an
Action's Done alike, so an answer worded for the other kind cannot send the count elsewhere.

The SELECT picks series, not answers: each item it finds counts with every copy of its
series, answered or open. One Check or one Action gets its SELECT from code, by the id its
series keeps when any copy is deleted.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import UnsafeQueryError, validated_read

from ..cards.model import Card, CardCheck
from ..checks.api import answer_run, newest_answers
from ..checks.model import Check, CheckOutcome
from ..schedules.api import is_open, total_count
from ..schedules.rules import repeats
from ..tags.api import Tag
from ..values.api import value_names
from .api import ANSWER_WORDS, Condition

# The views a condition's SELECT may read.
CONDITION_VIEWS = frozenset({"ai_checks", "ai_cards"})
# How many of the series found the review screen names before "and N more".
CONDITION_PREVIEW_TITLES = 3
# Items that are one Check or one Action, as the `when` session writes them: 'Check #12',
# or 'Check #12 «Meditated?»' with its title after the id.
ONE_ITEM = re.compile(r"(check|action) ?#(\d+)(?: [«\"“][^»\"”]*[»\"”])?", re.IGNORECASE)
# A word that says what items are, which an #id needs beside it.
ITEM_WORD = re.compile(r"\b(check|action)s?\b", re.IGNORECASE)
# A Check's yes and no, as its answers are stored.
OUTCOMES = {"yes": CheckOutcome.PASSED.value, "no": CheckOutcome.MISSED.value}


class ConditionError(ValueError):
    """A condition's SELECT that cannot find its items."""


async def item_names(session: AsyncSession) -> str:
    """The Tags and Values as they are stored, for the SELECT to spell: a match on a name is
    exact, and the owner's words need not be."""
    tags = sorted(await session.scalars(select(Tag.name)), key=str.casefold)
    values = list((await value_names(session)).values())
    return f"Tags: {', '.join(tags) or 'none'}\nValues: {', '.join(values) or 'none'}"


def items_view(kind: str) -> str:
    """The view an Action's items are read from, or a Check's."""
    return "ai_cards" if kind == "action" else "ai_checks"


def _series(kind: str) -> tuple[Any, Any]:
    """The items' table, and the series of each of its rows."""
    if kind == "action":
        return Card, func.coalesce(Card.repeat_series_id, Card.id)
    return Check, func.coalesce(Check.series_id, Check.id)


def checked_select(sql: str, kind: str) -> str:
    """The statement to run, or why it cannot find items of this kind."""
    try:
        statement, read = validated_read(sql, CONDITION_VIEWS)
    except UnsafeQueryError as error:
        raise ConditionError(str(error)) from error
    view = items_view(kind)
    if view not in read:
        raise ConditionError(f"The SELECT must read {view} and return its ids")
    return statement


def one_item(items: str) -> tuple[str, int] | None:
    """What one Check or one Action is, check or action, and its id; None for other items."""
    match = ONE_ITEM.fullmatch(items.strip())
    return (match[1].lower(), int(match[2])) if match else None


async def one_series_select(session: AsyncSession, items: str) -> tuple[str, str] | None:
    """What one Check or one Action is, and the SELECT of its whole series; None when the items
    are not one item, or it does not exist."""
    item = one_item(items)
    if item is None:
        return None
    kind, item_id = item
    model, series = _series(kind)
    root = await session.scalar(select(series).where(model.id == item_id))
    if root is None:
        return None
    return kind, f"SELECT id FROM {items_view(kind)} WHERE series_id = {root}"


async def found_ids(session: AsyncSession, condition: Condition) -> list[int]:
    """Every copy of each series the condition's SELECT finds an item of now."""
    statement = checked_select(condition.sql, condition.kind)
    try:
        result = await session.execute(text(statement))
    except SQLAlchemyError as error:
        raise ConditionError(f"The SELECT does not run: {getattr(error, 'orig', error)}") from error
    if "id" not in result.keys():
        raise ConditionError("The SELECT must return a column named id")
    try:
        found = {int(row["id"]) for row in result.mappings()}
    except (TypeError, ValueError) as error:
        raise ConditionError("The SELECT's ids must be whole numbers") from error
    model, series = _series(condition.kind)
    roots = select(series).where(model.id.in_(found))
    return list(await session.scalars(select(model.id).where(series.in_(roots)).order_by(model.id)))


async def _answers(session: AsyncSession, condition: Condition, ids: Sequence[int]) -> list[Any]:
    """The items' answers, newest first: Done of an Action, or a Check's Passed and Missed."""
    if condition.kind != "action":
        return await newest_answers(session, ids)
    return list(
        (
            await session.execute(
                select(Card.id)
                .where(Card.id.in_(ids), Card.completed_at.is_not(None))
                .order_by(Card.completed_at.desc(), Card.id.desc())
            )
        ).all()
    )


def _counted(condition: Condition, answers: Sequence[Any]) -> list[Any]:
    """The newest answers the condition counts: back to the first other one when in a row.
    An Action has no Missed, so its every Done counts, in a row or not."""
    if condition.kind == "action" or condition.answer == "any":
        return list(answers)
    if condition.in_row:
        return answer_run(answers, OUTCOMES[condition.answer])
    return [row for row in answers if row.outcome == OUTCOMES[condition.answer]]


async def count_now(
    session: AsyncSession, condition: Condition, ids: Sequence[int], item_id: int
) -> str | None:
    """How many answers the condition counted at this item's answer, as the Cue says it, or
    None when that answer is not one it counts or does not bring the count to a multiple —
    once, to the count itself.

    Each answer counts at its own place, in the order saved: the answers one commit saves
    together each reach their own count, so a multiple is said once and never skipped.
    """
    answers = await _answers(session, condition, ids)
    place = next((index for index, row in enumerate(answers) if row.id == item_id), None)
    if place is None:
        return None
    answers = answers[place:]
    counted = _counted(condition, answers)
    if not counted or counted[0].id != item_id:
        return None
    if len(counted) != condition.count if condition.once else len(counted) % condition.count:
        return None
    if condition.kind == "action":
        return f"{len(counted)} Done now"
    passed = sum(row.outcome == CheckOutcome.PASSED.value for row in answers)
    row = " in a row" if condition.in_row else ""
    return f"{len(counted)}{row} now; {passed} Passed and {len(answers) - passed} Missed in all"


async def never_reached(
    session: AsyncSession, condition: Condition, ids: Sequence[int]
) -> str | None:
    """Why the condition never comes to its count, or None when it can: once, the count is
    already past; or its items, one series, never reach it. They reach what it counts now,
    and every answer the series still has to come: one while it does not repeat and is open,
    or what is left of its Schedule's total. More than one series, or one without end, can
    reach any count.

    A Check runs as its own Schedule does, or else as the Action it hangs on.
    """
    counted = len(_counted(condition, await _answers(session, condition, ids)))
    if condition.once and counted >= condition.count:
        return (
            f"{condition.items} already has {count_words(condition, counted)}, "
            f"so 'after {condition.count}' is already past."
        )
    model, series = _series(condition.kind)
    roots = set(await session.scalars(select(series).where(model.id.in_(ids))))
    if len(roots) != 1:
        return None
    newest = await session.scalar(
        select(model).where(series.in_(roots)).order_by(model.id.desc()).limit(1)
    )
    runs: Any = newest
    if model is Check and newest.schedule_record is None:
        card = await session.scalar(
            select(Card)
            .join(CardCheck, CardCheck.card_id == Card.id)
            .where(CardCheck.check_id == newest.id)
        )
        runs = card or newest
    rule =runs.schedule_record.rule if runs.schedule_record is not None else None
    never = f"so a count of {condition.count} never comes."
    if not rule or not repeats(rule):
        if counted + int(is_open(newest)) >= condition.count:
            return None
        return f"{condition.items} does not repeat, {never}"
    if rule.get("limit") is None:
        return None
    left = rule["limit"] - await total_count(session, runs)
    if counted + left >= condition.count:
        return None
    answers = "1 answer" if left == 1 else f"{left} answers"
    return f"{condition.items} counts {counted} now and has {answers} left, {never}"


async def finds_now(session: AsyncSession, condition: Condition) -> str:
    """The Reminder screen's line: what the SELECT finds now, or why it counts nothing."""
    try:
        ids = await found_ids(session, condition)
    except ConditionError as error:
        return f"⚠️ It counts nothing: {error}. Ask Safwa to set it again."
    return f"🔎 Finds now: {await finds_text(session, condition, ids)}"


async def finds_text(session: AsyncSession, condition: Condition, ids: Sequence[int]) -> str:
    """What the SELECT finds now: the newest title of each series, up to
    CONDITION_PREVIEW_TITLES of them, and how many more; then what it counts now."""
    model, series = _series(condition.kind)
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
    counted = len(_counted(condition, await _answers(session, condition, ids)))
    return (
        shown
        + (f" and {more} more" if more > 0 else "")
        + f". {count_words(condition, counted)} now."
    )


def count_words(condition: Condition, count: int) -> str:
    """A count of the condition's answers as the screens say it: 12 Passed in a row, 3 Done."""
    one, many = ANSWER_WORDS[condition.kind, condition.answer]
    row = " in a row" if condition.in_row else ""
    return f"{count} {one if count == 1 else many}{row}"
