"""The short panel beside each Constellation picture: what was done and what is left.

The sky's and a Value's panels look back `SKY_WINDOW_DAYS`; a Goal's covers all time under it.
A Done item counts for a Value through anything it stands under, finished or not, so a closed
Goal's work still counts for what it served. Repeating Actions stay out of a Goal's share, which
counts what can be finished: a habit Done thirty times is said apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.charts import day_month
from ..cards.api import PLANNED_STAGES, parents_of
from ..cards.model import Card, CardCheck, CardKind, CardStage, effort_label, minutes_label
from ..checks.model import Check, CheckOutcome
from ..profile.api import effort_tracking_on, time_tracking_on
from ..schedules.api import repeating
from ..values.model import CardValue, CheckValue
from .weight import SKY_WINDOW_DAYS, Item, Weighed

MOVING = {stage.value for stage in PLANNED_STAGES}


@dataclass(frozen=True, slots=True)
class Panel:
    heading: str
    # Each row's mark and its words.
    rows: tuple[tuple[str, str], ...]
    # A Goal's share of its one-off Actions Done, drawn as a bar over the rows.
    share: float | None = None


@dataclass(frozen=True, slots=True)
class History:
    """The Actions Done and the Checks answered in the window, with the Values each counts for."""

    actions: list[Card]
    checks: list[Check]
    counts: dict[Item, frozenset[int]]
    effort: bool
    time: bool


async def read_history(session: AsyncSession, now: datetime) -> History:
    start = now - timedelta(days=SKY_WINDOW_DAYS)
    actions = list(await session.scalars(
        select(Card).where(Card.kind == CardKind.ACTION.value, Card.completed_at >= start)
    ))
    checks = list(await session.scalars(
        select(Check).where(Check.outcome.is_not(None), Check.resolved_at >= start)
    ))
    parents = await parents_of(session)
    carried: dict[int, set[int]] = {}
    for card_id, value_id in await session.execute(select(CardValue.card_id, CardValue.value_id)):
        carried.setdefault(card_id, set()).add(value_id)
    counted: dict[int, frozenset[int]] = {}

    def card_counts(card_id: int) -> frozenset[int]:
        if card_id not in counted:
            counted[card_id] = frozenset(carried.get(card_id, ())).union(
                *(card_counts(parent) for parent in parents.get(card_id, ()))
            )
        return counted[card_id]

    wanted = [check.id for check in checks]
    on = dict((await session.execute(
        select(CardCheck.check_id, CardCheck.card_id).where(CardCheck.check_id.in_(wanted))
    )).all())
    own: dict[int, set[int]] = {}
    for check_id, value_id in await session.execute(
        select(CheckValue.check_id, CheckValue.value_id).where(CheckValue.check_id.in_(wanted))
    ):
        own.setdefault(check_id, set()).add(value_id)
    counts: dict[Item, frozenset[int]] = {("card", card.id): card_counts(card.id) for card in actions}
    counts |= {
        ("check", check.id): frozenset(own.get(check.id, ())).union(
            card_counts(on[check.id]) if check.id in on else ()
        )
        for check in checks
    }
    return History(
        actions, checks, counts, await effort_tracking_on(session), await time_tracking_on(session)
    )


def sky_panel(weighed: Weighed, history: History) -> Panel:
    zero = sum(not weighed.of(value.id) for value in weighed.tree.values)
    loose = sum(not history.counts[("card", card.id)] for card in history.actions)
    rows = [
        *_window_rows(history.actions, history.checks, _open_actions(weighed, None), history),
        ("·", f"No Value: {loose} Done"),
        ("·", f"Values at 0: {zero}"),
    ]
    return Panel(f"Last {SKY_WINDOW_DAYS} days", tuple(rows))


def value_panel(weighed: Weighed, history: History, value: int) -> Panel:
    actions = [card for card in history.actions if value in history.counts[("card", card.id)]]
    checks = [check for check in history.checks if value in history.counts[("check", check.id)]]
    rows = _window_rows(actions, checks, _open_actions(weighed, value), history)
    repeating_here = [
        item for item in weighed.runs
        if item[0] == "card" and value in weighed.counts[item]
    ]
    if repeating_here:
        weekly = sum(weighed.runs[item] for item in repeating_here) * 7 / SKY_WINDOW_DAYS
        rows.append(("↻", f"{_counted(len(repeating_here), 'repeating Action')} · {weekly:.0f} a week"))
    return Panel(f"Last {SKY_WINDOW_DAYS} days", tuple(rows))


def _open_actions(weighed: Weighed, value: int | None) -> list[Card]:
    return [
        card for card in weighed.tree.cards
        if card.kind == CardKind.ACTION.value
        and (value is None or value in weighed.counts[("card", card.id)])
    ]


def _window_rows(
    done: list[Card], answered: list[Check], open_actions: list[Card], history: History
) -> list[tuple[str, str]]:
    passed = sum(check.outcome == CheckOutcome.PASSED.value for check in answered)
    moving = sum(card.effective_stage in MOVING for card in open_actions)
    rows = [
        ("✓", f"{len(done)} Done"),
        ("◆", f"{passed} of {_counted(len(answered), 'Check')} passed"),
        ("○", f"{len(open_actions)} open · {moving} in Sprint or Today"),
    ]
    if history.effort:
        rows.append(("⚡", f"{effort_label(_points(done))} EP Done"))
    if history.time:
        rows.append(("⏱", f"{minutes_label(_minutes(done))} tracked"))
    return rows


async def read_below(session: AsyncSession, goal_id: int) -> tuple[dict[int, list[int]], dict[int, Card]]:
    """Every Card under the Goal, at any stage and archived too, and each Card's children."""
    children: dict[int, list[int]] = {}
    for card_id, ids in (await parents_of(session)).items():
        for parent in ids:
            children.setdefault(parent, []).append(card_id)
    below: set[int] = set()
    waiting = list(children.get(goal_id, ()))
    while waiting:
        current = waiting.pop()
        if current not in below:
            below.add(current)
            waiting.extend(children.get(current, ()))
    cards = {
        card.id: card for card in await session.scalars(select(Card).where(Card.id.in_(below)))
    }
    return children, cards


def actions_under(goal_id: int, children: Mapping[int, list[int]], cards: Mapping[int, Card]) -> list[Card]:
    """The distinct Actions anywhere under the Goal."""
    seen: set[int] = set()
    waiting = list(children.get(goal_id, ()))
    while waiting:
        current = waiting.pop()
        if current not in seen and current in cards:
            seen.add(current)
            waiting.extend(children.get(current, ()))
    return [cards[card_id] for card_id in sorted(seen) if cards[card_id].kind == CardKind.ACTION.value]


def one_off_share(actions: Iterable[Card]) -> float | None:
    """The share of the one-off Actions that are Done."""
    done, total = _one_off(actions)
    return done / total if total else None


def _one_off(actions: Iterable[Card]) -> tuple[int, int]:
    """How many one-off Actions are Done, and how many there are; an archived one is Done."""
    one_off = [action for action in actions if not repeating(action)]
    return sum(action.effective_stage == CardStage.DONE.value for action in one_off), len(one_off)


def goal_panel(
    goal: Card, actions: list[Card], today: date, tz: ZoneInfo, *, effort: bool, time: bool
) -> Panel:
    done = [action for action in actions if action.effective_stage == CardStage.DONE.value]
    open_actions = [action for action in actions if action.effective_stage != CardStage.DONE.value]
    finished, counted = _one_off(actions)
    share = finished / counted if counted else None
    rows = [
        ("✓", f"{finished} of {_counted(counted, 'Action')} Done"
         + (f" · {round(share * 100)}%" if share is not None else "")),
        ("○", f"{len(open_actions)} open · {sum(action.blocked for action in open_actions)} Blocked"),
    ]
    repeats = sum(repeating(action) for action in done)
    if repeats:
        rows.append(("↻", f"Repeating Actions Done {_counted(repeats, 'time')}"))
    if effort:
        rows.append(("⚡", f"{effort_label(_points(done))} EP Done · "
                           f"{effort_label(_points(open_actions))} ahead"))
    if time:
        rows.append(("⏱", f"{minutes_label(_minutes(done))} tracked"))
    if goal.deadline_at is not None:
        due = goal.deadline_at.astimezone(tz).date()
        left = (due - today).days
        rows.append(("⏳", f"Deadline {day_month(due)} · "
                           + (f"{_counted(left, 'day')} left" if left >= 0 else f"{_counted(-left, 'day')} over")))
    rows.append(("·", f"Created {day_month(goal.created_at.astimezone(tz).date())}"))
    return Panel("All time", tuple(rows), share)


def _points(cards: Iterable[Card]) -> float:
    return sum(card.effort_points or 0 for card in cards)


def _minutes(cards: Iterable[Card]) -> int:
    return sum(card.tracked_mins or 0 for card in cards)


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
