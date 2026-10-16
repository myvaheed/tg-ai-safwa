"""The common order of Goals and Actions, including their inherited Value scores."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow

from ...foundation.workspace import require_workspace
from ..planning.model import Sprint, SprintCommitment
from ..values.model import CardValue, Value
from .model import CARD_TREE_DEPTH_MAX, Card, CardKind, CardParent, CardStage, Priority

# How many days ahead an appointment is near enough to belong in Today: today and tomorrow.
SCHEDULE_NOTICE_DAYS = 1
ACTION_WEEK_DAYS = 7
GOAL_URGENT_DAYS = 14
PLANNING_GOAL_DAYS = 30
GOAL_QUARTER_DAYS = 90

_PRIORITIES = {priority.value: rank for rank, priority in enumerate(Priority)}
# While a Sprint runs, the focus order reads Today first, then the Sprint, then the rest.
_FOCUS_STAGES = {CardStage.TODAY.value: 0, CardStage.SPRINT.value: 1}


@dataclass(frozen=True, slots=True)
class _Graph:
    parents: dict[int, list[int]]
    goal_children: dict[int, list[int]]
    value_orders: dict[int, int]
    carried: dict[int, list[int]]

    def reached(self, card_id: int) -> dict[int, int]:
        """Each Value the Card or an ancestor carries, at its shortest upward distance."""
        seen = {card_id}
        nearest: dict[int, int] = {}
        waiting = deque([(card_id, 0)])
        while waiting:
            current, distance = waiting.popleft()
            for value_id in self.carried.get(current, ()):
                nearest.setdefault(value_id, distance)
            for parent in self.parents.get(current, ()):
                if parent not in seen:
                    seen.add(parent)
                    waiting.append((parent, distance + 1))
        return nearest


async def _graph(session: AsyncSession) -> _Graph:
    goals = set(await session.scalars(select(Card.id).where(Card.kind == CardKind.GOAL.value)))
    parents: dict[int, list[int]] = {}
    goal_children: dict[int, list[int]] = {}
    for card_id, parent_id in await session.execute(select(CardParent.card_id, CardParent.parent_id)):
        parents.setdefault(card_id, []).append(parent_id)
        if card_id in goals:
            goal_children.setdefault(parent_id, []).append(card_id)
    carried: dict[int, list[int]] = {}
    for card_id, value_id in await session.execute(select(CardValue.card_id, CardValue.value_id)):
        carried.setdefault(card_id, []).append(value_id)
    return _Graph(
        parents=parents,
        goal_children=goal_children,
        value_orders={
            value_id: order
            for value_id, order in await session.execute(select(Value.id, Value.order))
        },
        carried=carried,
    )


async def served_values(session: AsyncSession, card_ids: Iterable[int]) -> dict[int, list[int]]:
    """The Values each Card serves, its own and its ancestors', highest first."""
    graph = await _graph(session)
    return {
        card_id: sorted(graph.reached(card_id), key=graph.value_orders.__getitem__)
        for card_id in card_ids
    }


async def order_cards(
    session: AsyncSession,
    cards: Iterable[Card],
    *,
    now: datetime | None = None,
    focus: bool = False,
) -> list[Card]:
    """Goals first, then Actions, each in its own date/priority/key/Value order.

    Value order 1 is strongest. A distinct Value contributes
    `(N - order + 1) * 2 ** (max_distance - nearest_distance)`, an exact integer
    multiple of `(N - order + 1) / 2 ** nearest_distance`. Only the Card itself and
    its ancestors carry its Values; the number of paths and its children add nothing.

    `focus` is the order of what to work on rather than of what to show: a Goal with
    fewer levels under it comes before a taller one, and while a Sprint runs an Action
    in Today comes before one in the Sprint, and both before the rest.
    """
    selected = list(cards)
    if not selected:
        return []
    workspace = await require_workspace(session)
    local_now = (now or utcnow()).astimezone(ZoneInfo(workspace.timezone))
    today = local_now.date()
    sprint = (
        await session.get(Sprint, workspace.active_sprint_id)
        if workspace.active_sprint_id else None
    )
    keys = set(
        await session.scalars(
            select(SprintCommitment.card_id).where(
                SprintCommitment.sprint_id == sprint.id, SprintCommitment.key_action.is_(True)
            )
        )
    ) if sprint is not None else set()
    graph = await _graph(session)
    count = len(graph.value_orders)

    @cache
    def height(card_id: int) -> int:
        return 1 + max((height(child) for child in graph.goal_children.get(card_id, ())), default=0)

    def score(card_id: int) -> int:
        return sum(
            (count - graph.value_orders[value_id] + 1) << (CARD_TREE_DEPTH_MAX - 1 - distance)
            for value_id, distance in graph.reached(card_id).items()
        )

    def goal_group(card: Card) -> int:
        if card.deadline_at is None:
            return 2
        due = card.deadline_at.astimezone(local_now.tzinfo).date()
        if due <= today:
            return 0
        if sprint is None:
            if due < today + timedelta(days=PLANNING_GOAL_DAYS):
                return 0
        elif due <= sprint.planned_end_date or due < today + timedelta(days=GOAL_URGENT_DAYS):
            return 0
        return 1 if due <= today + timedelta(days=GOAL_QUARTER_DAYS) else 2

    def key(card: Card) -> tuple:
        priority = _PRIORITIES[card.priority]
        values = -score(card.id)
        tail = (card.created_at, card.id)
        if card.kind == CardKind.GOAL.value:
            deadline = card.deadline_at.timestamp() if card.deadline_at else float("inf")
            level = height(card.id) if focus else -height(card.id)
            group = goal_group(card)
            if group == 0:
                return (0, 0, group, deadline, level, priority, values, *tail)
            if group == 1:
                return (0, 0, group, priority, level, deadline, values, *tail)
            return (0, 0, group, priority, level, values, deadline, *tail)
        stage = _FOCUS_STAGES.get(card.effective_stage, 2) if focus and sprint is not None else 0
        appointment = card.scheduled_at
        when = appointment.timestamp() if appointment else float("inf")
        due = appointment.astimezone(local_now.tzinfo).date() if appointment else None
        ordinary = card.id not in keys
        if due is not None and due <= today + timedelta(days=SCHEDULE_NOTICE_DAYS):
            return (1, stage, 0, when, priority, ordinary, values, *tail)
        if due is not None and due <= today + timedelta(days=ACTION_WEEK_DAYS):
            return (1, stage, 1, priority, when, ordinary, values, *tail)
        return (1, stage, 2, priority, ordinary, values, when, *tail)

    return sorted(selected, key=key)
