"""Reminders on answers: the items they count, and what an answer makes of them.

The items are Checks or Actions named by #id, all of one kind, as the `when` session writes
them: 'Check #12', or 'Check #12 «Meditated?», Check #14'. Each is kept as its series, by the
id the series keeps when any copy is deleted, so every copy counts, answered or open, and an
item made later counts only as a copy of one of them. A group in words is the subagent's to
list by #id before it sets the Reminder.

What the items are is the items' to say, never the answer's: yes is a Check's Passed and an
Action's Done alike, so an answer worded for the other kind cannot send the count elsewhere.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cards.model import Card, CardCheck
from ..checks.api import answer_run, newest_answers
from ..checks.model import Check, CheckOutcome
from ..schedules.api import is_open, total_count
from ..schedules.rules import repeats
from .api import ANSWER_WORDS, Condition

# How many of the series found the review screen names before "and N more".
CONDITION_PREVIEW_TITLES = 3
# One Check or one Action as the `when` session writes it: 'Check #12', or 'Check #12
# «Meditated?»' with its title after the id.
ITEM = re.compile(r"(check|action) ?#(\d+)(?: [«\"“][^»\"”]*[»\"”])?", re.IGNORECASE)
# What may stand between two of them.
BETWEEN = re.compile(r"(?:\s|,|;|&|\band\b|\bи\b)*", re.IGNORECASE)
# A word that says what items are, which an #id needs beside it.
ITEM_WORD = re.compile(r"\b(check|action)s?\b", re.IGNORECASE)
# A Check's yes and no, as its answers are stored.
OUTCOMES = {"yes": CheckOutcome.PASSED.value, "no": CheckOutcome.MISSED.value}
NAME_BY_ID = "Name each item by #id, as 'Check #1, Check #2'. Find a group's items with query_data."
ONE_KIND = "Count Checks and Actions in separate Reminders."


class ConditionError(ValueError):
    """Items that are not Checks or Actions by #id that exist."""


def _series(kind: str) -> tuple[Any, Any]:
    """The items' table, and the series of each of its rows."""
    if kind == "action":
        return Card, func.coalesce(Card.repeat_series_id, Card.id)
    return Check, func.coalesce(Check.series_id, Check.id)


def item_kinds(items: str) -> set[str]:
    """What the items' #ids say they are: check, action, both or neither."""
    return {match[1].lower() for match in ITEM.finditer(items)}


async def items_series(session: AsyncSession, items: str) -> tuple[str, tuple[int, ...]]:
    """What the items are, check or action, and the series of each; ConditionError for words
    that are not Checks or Actions by #id, for both kinds, or for an #id that does not exist."""
    found = list(ITEM.finditer(items))
    if not found or not BETWEEN.fullmatch(ITEM.sub(" ", items)):
        raise ConditionError(NAME_BY_ID)
    kinds = item_kinds(items)
    if len(kinds) > 1:
        raise ConditionError(ONE_KIND)
    kind = kinds.pop()
    ids = [int(match[2]) for match in found]
    model, series = _series(kind)
    roots = dict((await session.execute(select(model.id, series).where(model.id.in_(ids)))).all())
    for item_id in ids:
        if item_id not in roots:
            raise ConditionError(f"{kind.title()} #{item_id} does not exist.")
    return kind, tuple(sorted(set(roots.values())))


async def found_ids(session: AsyncSession, condition: Condition) -> list[int]:
    """Every copy of each of the condition's series there is now."""
    model, series = _series(condition.kind)
    return list(
        await session.scalars(
            select(model.id).where(series.in_(condition.series)).order_by(model.id)
        )
    )


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
    if len(condition.series) != 1:
        return None
    model, series = _series(condition.kind)
    newest = await session.scalar(
        select(model).where(series.in_(condition.series)).order_by(model.id.desc()).limit(1)
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
    """The Reminder screen's line: what it counts now."""
    return f"🔎 Finds now: {await finds_text(session, condition, await found_ids(session, condition))}"


async def finds_text(session: AsyncSession, condition: Condition, ids: Sequence[int]) -> str:
    """What the condition finds now: the newest title of each series, up to
    CONDITION_PREVIEW_TITLES of them, and how many more; then what it counts now."""
    model, series = _series(condition.kind)
    named: dict[int, str] = {}
    for root, title in await session.execute(
        select(series, model.title).where(model.id.in_(ids)).order_by(model.id.desc())
    ):
        named.setdefault(root, title)
    if not named:
        return "Nothing: its items were deleted."
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
