"""How much open work carries each Value: what the Constellation sizes its stars by.

An open item counts for a Value when it, the Card it is on or an open Goal above them carries
that Value, once however many paths lead to it. A Goal weighs one more than its `tree_level`,
an Action or a Check one, and one that repeats the square root of the times its Schedule plans
it in the window ahead: a daily habit outweighs a Goal, a monthly one does not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ..cards.api import tree_levels
from ..cards.model import Card, CardKind
from ..checks.model import Check
from ..schedules.api import planned_runs, repeating, workspace_zone
from .graph import Tree, read_tree

# The days a repeating item's Schedule is read ahead for its weight, and the days the
# Constellation's panels look back.
SKY_WINDOW_DAYS = 28
# The times an item repeating after each completion counts as planned in that window.
AFTER_COMPLETION_RUNS = 4

Item = tuple[str, int]


@dataclass(frozen=True, slots=True)
class Weighed:
    tree: Tree
    weight: dict[Item, float]
    # The Values each open item counts for, and the ones it carries itself.
    counts: dict[Item, frozenset[int]]
    own: dict[Item, tuple[int, ...]]
    levels: dict[int, int]
    # The times each repeating item is planned in the window ahead.
    runs: dict[Item, int]

    def of(self, value: int) -> float:
        return sum(self.weight[item] for item, ids in self.counts.items() if value in ids)

    def shared(self, first: int, second: int) -> float:
        return sum(
            self.weight[item] for item, ids in self.counts.items() if {first, second} <= ids
        )


async def weigh(session: AsyncSession, now: datetime) -> Weighed:
    tree = await read_tree(session)
    tz = await workspace_zone(session)
    levels = await tree_levels(session)
    end = now + timedelta(days=SKY_WINDOW_DAYS)
    entities: dict[Item, Card | Check] = {("card", card.id): card for card in tree.cards} | {
        ("check", check.id): check for check in tree.checks
    }
    runs: dict[Item, int] = {}
    for item, entity in entities.items():
        if (isinstance(entity, Check) or entity.kind == CardKind.ACTION.value) and repeating(entity):
            planned = planned_runs(entity, now, end, tz)
            runs[item] = AFTER_COMPLETION_RUNS if planned is None else planned

    def weight(item: Item, entity: Card | Check) -> float:
        if isinstance(entity, Card) and entity.kind == CardKind.GOAL.value:
            return 1.0 + levels.get(entity.id, 1)
        return max(1.0, math.sqrt(runs[item])) if item in runs else 1.0

    cards = {card.id: card for card in tree.cards}
    own: dict[Item, tuple[int, ...]] = {
        ("card", card_id): tuple(tree.card_values.get(card_id, [])) for card_id in cards
    } | {("check", check.id): tuple(tree.check_values.get(check.id, [])) for check in tree.checks}
    counted: dict[int, frozenset[int]] = {}

    def card_counts(card_id: int) -> frozenset[int]:
        if card_id not in counted:
            above = [parent for parent in tree.card_parents.get(card_id, ()) if parent in cards]
            counted[card_id] = frozenset(own[("card", card_id)]).union(
                *(card_counts(parent) for parent in above)
            )
        return counted[card_id]

    counts: dict[Item, frozenset[int]] = {("card", card_id): card_counts(card_id) for card_id in cards}
    for check in tree.checks:
        on = tree.check_cards.get(check.id)
        counts[("check", check.id)] = frozenset(own[("check", check.id)]).union(
            card_counts(on.id) if on is not None and on.id in cards else ()
        )
    weights = {item: weight(item, entity) for item, entity in entities.items()}
    return Weighed(tree, weights, counts, own, levels, runs)
