"""What another feature may ask of Cards.

A stage is a Card's own word, so the enum comes through this door with the two questions
Planning asks of it. Nothing here
imports the Card use cases: Planning reads through this door and Cards writes through
Planning's, which is what keeps the two acyclic.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_agent_shell.foundation.errors import DomainError

from ...foundation.marks import title_marks
from ...foundation.method import Example, Method
from ..schedules.api import ACTION_DAILY_EXECUTIONS_MAX
from .model import TERMINAL_STAGES as TERMINAL_STAGES
from .model import Card as Card
from .model import CardKind, CardParent
from .model import CardStage as CardStage
from .model import effort_label as effort_label
from .model import minutes_label as minutes_label
from .ordering import SCHEDULE_NOTICE_DAYS as SCHEDULE_NOTICE_DAYS
from .ordering import order_cards as order_cards
from .ordering import served_values as served_values

PLANNED_STAGES = (CardStage.SPRINT, CardStage.TODAY)
# A Done list reads newest first; a Goal Done through its children may have no completion time.
FINISHED_LAST = (Card.completed_at.desc().nulls_last(), Card.updated_at.desc(), Card.id.desc())


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


@dataclass(frozen=True, slots=True)
class FinishedAction:
    """One finished Action as a count over time reads it."""

    completed_at: datetime
    effort_points: float | None
    categories: frozenset[str]
    energy_types: frozenset[str]
    # The Values it serves: its own, and those every Goal above it carries.
    value_ids: frozenset[int]


async def every_finished_action(session: AsyncSession) -> list[FinishedAction]:
    """Every Action ever finished, archived or not, oldest first."""
    goals = {
        card.id: card
        for card in await session.scalars(
            select(Card)
            .where(Card.kind != CardKind.ACTION.value)
            .options(selectinload(Card.values))
        )
    }
    parents = await parents_of(session)

    def served(card: Card) -> frozenset[int]:
        ids: set[int] = set()
        seen: set[int] = set()
        waiting = [card.id]
        while waiting:
            current = waiting.pop()
            if current in seen:
                continue
            seen.add(current)
            holder = goals.get(current, card if current == card.id else None)
            if holder is not None:
                ids.update(link.value_id for link in holder.values)
            waiting.extend(parents.get(current, ()))
        return frozenset(ids)

    actions = await session.scalars(
        select(Card)
        .where(
            Card.kind == CardKind.ACTION.value,
            Card.effective_stage == CardStage.DONE.value,
            Card.completed_at.is_not(None),
        )
        .options(
            selectinload(Card.values),
            selectinload(Card.categories),
            selectinload(Card.energy_types),
        )
        .order_by(Card.completed_at, Card.id)
    )
    return [
        FinishedAction(
            completed_at=action.completed_at,
            effort_points=action.effort_points,
            categories=frozenset(link.category for link in action.categories),
            energy_types=frozenset(link.energy_type for link in action.energy_types),
            value_ids=served(action),
        )
        for action in actions
        if action.completed_at is not None
    ]


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


async def tree_level(session: AsyncSession, kind: str | None, card_id: int | None) -> int:
    """A Goal's 🎯 index: the Goals in its deepest chain down, itself included. 0 for an Action.

    `ai_cards.tree_level` is the same number in SQL.
    """
    if kind != CardKind.GOAL.value:
        return 0
    if card_id is None:
        return 1
    children = await session.scalars(
        select(Card.id)
        .join(CardParent, CardParent.card_id == Card.id)
        .where(CardParent.parent_id == card_id, Card.kind == CardKind.GOAL.value)
    )
    return 1 + max([await tree_level(session, kind, child) for child in children], default=0)


async def parents_of(
    session: AsyncSession, card_ids: Iterable[int] | None = None
) -> dict[int, list[int]]:
    """The Goals each Card stands under, by Card id; every Card's when no ids are named.

    A Card with no parent is not a key.
    """
    query = select(CardParent.card_id, CardParent.parent_id).order_by(
        CardParent.card_id, CardParent.parent_id
    )
    if card_ids is not None:
        wanted = list(card_ids)
        if not wanted:
            return {}
        query = query.where(CardParent.card_id.in_(wanted))
    found: dict[int, list[int]] = {}
    for card_id, parent_id in await session.execute(query):
        found.setdefault(card_id, []).append(parent_id)
    return found


@dataclass(frozen=True, slots=True)
class ForestRow:
    """One Card in a drawn list of Cards: how deep it stands, and its other parents there."""

    depth: int
    card: Card
    also_under: tuple[Card, ...]


def forest_rows(cards: Sequence[Card], parents: Mapping[int, Iterable[int]]) -> list[ForestRow]:
    """Every Card once, in the order given, each under the last of its parents to be shown.

    A Card comes right under that parent, one level below it, once every parent it has in
    the list is shown; the others are named beside it. A Card with no parent in the list
    stands at the top, where it is in the order.
    """
    by_id = {card.id: card for card in cards}
    position = {card.id: index for index, card in enumerate(cards)}
    listed = {
        card.id: sorted(
            {parent for parent in parents.get(card.id, ()) if parent in by_id},
            key=position.__getitem__,
        )
        for card in cards
    }
    children: dict[int, list[int]] = {}
    for card in cards:
        for parent in listed[card.id]:
            children.setdefault(parent, []).append(card.id)
    waiting = {card_id: len(held) for card_id, held in listed.items()}
    placed: set[int] = set()
    rows: list[ForestRow] = []

    def visit(card_id: int, depth: int, via: int | None) -> None:
        placed.add(card_id)
        others = tuple(by_id[parent] for parent in listed[card_id] if parent != via)
        rows.append(ForestRow(depth, by_id[card_id], others))
        # Every child counts this parent as shown before any of them is walked, so a child
        # that also stands under a sibling comes under that sibling, shown after this one.
        for child in children.get(card_id, ()):
            waiting[child] -= 1
        for child in children.get(card_id, ()):
            if not waiting[child] and child not in placed:
                visit(child, depth + 1, card_id)

    for card in cards:
        if not listed[card.id]:
            visit(card.id, 0, None)
    return rows


async def goal_tree(
    session: AsyncSession, actions: Sequence[Card], *, now: datetime | None = None
) -> list[ForestRow]:
    """`actions`, in the order given, under the Goals above them, each Card once.

    A Card under several Goals stands under the one with the most of `actions` under it, the
    higher in the common Card order on a tie, and names the others. Only the Goals on those
    chains are shown, each where the first Action under it stands.
    """
    if not actions:
        return []
    goals = await order_cards(
        session, await session.scalars(select(Card).where(Card.kind == CardKind.GOAL.value)), now=now
    )
    by_id = {card.id: card for card in [*goals, *actions]}
    rank = {card.id: position for position, card in enumerate(goals)}
    parents = await parents_of(session)
    weight = dict.fromkeys(rank, 0)
    for action in actions:
        above: set[int] = set()
        waiting = list(parents.get(action.id, ()))
        while waiting:
            if (goal_id := waiting.pop()) not in above:
                above.add(goal_id)
                waiting.extend(parents.get(goal_id, ()))
        for goal_id in above:
            weight[goal_id] += 1
    anchor: dict[int, int | None] = {}
    children: dict[int | None, list[int]] = {}

    def place(card_id: int) -> None:
        if card_id in anchor:
            return
        anchor[card_id] = min(
            parents.get(card_id, ()), key=lambda goal_id: (-weight[goal_id], rank[goal_id]), default=None
        )
        if anchor[card_id] is not None:
            place(anchor[card_id])
        children.setdefault(anchor[card_id], []).append(card_id)

    for action in actions:
        place(action.id)
    rows: list[ForestRow] = []

    def visit(card_id: int, depth: int) -> None:
        others = sorted(
            (parent for parent in parents.get(card_id, ()) if parent != anchor[card_id]),
            key=rank.__getitem__,
        )
        rows.append(ForestRow(depth, by_id[card_id], tuple(by_id[parent] for parent in others)))
        for child in children.get(card_id, ()):
            visit(child, depth + 1)

    for root in children.get(None, ()):
        visit(root, 0)
    return rows


GOAL = Method(
    name="Goal",
    definition=(
        "Goal: one specific result that takes more than one day and can be Done, "
        "attainable with the user's resources and meaningful to them."
    ),
    criteria=(
        "names one concrete result, not a direction;",
        "is a result, not an ongoing practice or a rule;",
        "takes more than one day; work for one day is an Action;",
        "has an observable Done: the title or Note says what fact shows it;",
        "as a Subgoal, its result moves each parent's result forward.",
    ),
    leaves=("Do not judge whether the Goal is attainable or worth doing.",),
    writing=(
        "A Goal may be root-level or under one or more Goals. With a parent it is displayed as a "
        "Subgoal. An Action may stand under any Goals or alone and has no children.",
        "An ongoing practice is a repeating Action or Check, not a Goal.",
        "For a new or reframed Goal or Subgoal, put the result in `title` and an observable "
        "completion criterion in `note`.",
        "Deadline optional. Never invent targets, resources or deadlines.",
    ),
    examples=(
        Example('"Run a half marathon"', clear=True),
        Example('"Improve my English", Note "Pass IELTS with 7.0"', clear=True),
        Example(
            '"Learn Spanish"',
            clear=False,
            verdict="Done is not observable.",
            ask="Ask what result shows it, such as passing DELE B2.",
        ),
        Example(
            '"Get fit"',
            clear=False,
            verdict="A direction, not a result.",
            ask="Ask what result would show it, such as a 5 km run under 30 minutes; "
            "else suggest a Value such as Health.",
        ),
        Example(
            '"Meditate daily"',
            clear=False,
            verdict="An ongoing practice.",
            ask="Suggest a repeating Action, or a Check to track it; ask whether a result is "
            "behind it, such as 30 days in a row.",
        ),
        Example(
            '"Never be late"',
            clear=False,
            verdict="A rule kept every day.",
            ask="Suggest a repeating Check such as On time?; ask whether a result is behind "
            "it, such as 30 meetings on time in a row.",
        ),
        Example(
            '"Call the bank"',
            clear=False,
            verdict="Fits in one day.",
            ask="Suggest an Action instead.",
        ),
        Example(
            '"Launch the shop and find a job"',
            clear=False,
            verdict="Two results.",
            ask="Suggest two Goals.",
        ),
        Example(
            '"Holiday in Bali", parent "Earn $20k"',
            clear=False,
            verdict="Does not move its parent forward: it is what the money is for.",
            ask="Suggest a Goal of its own, or ask how it helps earn $20k.",
        ),
    ),
)

ACTION = Method(
    name="Action",
    definition=(
        "Action: work that fits in one day. A repeating Action fits in one day each time."
    ),
    criteria=(
        "fits in one day; a result that takes longer is a Goal;",
        "one concrete step that can be started, not a direction such as Work on my health;",
        "work to do, not a state to observe; a state such as Back straight is a Check;",
        "moves its Goals forward, when it has any.",
    ),
    leaves=("Do not judge whether the Action is worth doing.",),
    writing=(
        f"An Action repeats at most {ACTION_DAILY_EXECUTIONS_MAX} times a day; more often is a Check.",
    ),
    examples=(
        Example('"Book a flat viewing", chores, cognitive, Goal "Move to a new flat"', clear=True),
        Example(
            '"Write the thesis"',
            clear=False,
            verdict="Takes more than one day, so a Goal.",
            ask="Suggest a Goal with Actions under it, such as Write chapter 1.",
        ),
        Example(
            '"Work on my health"',
            clear=False,
            verdict="A direction, not a step.",
            ask="Ask what one step to take, such as Book a check-up.",
        ),
        Example(
            '"Back straight"',
            clear=False,
            verdict="A state to observe, not work.",
            ask="Suggest a Check instead.",
        ),
        Example(
            '"Buy a new phone", Goal "Save $5k"',
            clear=False,
            verdict="Works against its Goal.",
            ask="Ask whether it belongs under another Goal or on its own.",
        ),
    ),
)
