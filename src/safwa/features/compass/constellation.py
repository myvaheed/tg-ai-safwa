"""What the Constellation shows, and where: the sky of Values, one Value's orbits, and one
Goal's branch.

The sky puts each Value on one arc in the owner's order, its area as big as its weight. A
Value's orbits put what counts for it around it, the highest items nearest; each item stands
once, in the sector of the first item above it that reaches it, so the lines out of the middle
never cross, and each solid line is as thick as the weight it brings. A branch reads from left
to right, each parent beside the middle of its children and the Goals above it dashed to its
left.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.charts import day_month
from ..cards.api import parents_of
from ..cards.model import Card, CardKind
from ..checks.model import Check
from ..profile.api import effort_tracking_on, time_tracking_on
from ..schedules.api import repeating, workspace_zone
from .graph import Tree, plain_findings, read_tree
from .panels import (
    MOVING,
    Panel,
    actions_under,
    goal_panel,
    one_off_share,
    read_below,
    read_history,
    sky_panel,
    value_panel,
)
from .weight import Item, Weighed, weigh

# The picture's own units are inches. The panel stands at the right of every picture.
PANEL_WIDTH = 2.9
PANEL_MARGIN = 0.45
# The sky: the arc's left end, the least room between two Values and the least span, where the
# arc rests and how high it rises, how far neighbours step up and down so their names do not
# meet, the smallest and largest star, and the picture's height.
ARC_LEFT = 1.2
ARC_STEP = 1.5
ARC_SPAN = 7.4
ARC_BASE = 2.5
ARC_RISE = 1.5
ARC_ZIGZAG = 0.35
STAR_RADIUS = (0.06, 0.36)
SKY_HEIGHT = 6.8
# A Value's orbits: the picture, its middle, the nearest and the farthest orbit, and how much
# flatter an orbit is than wide, which tilts it.
ORBITS_SIZE = (14.0, 7.8)
ORBITS_CENTRE = (5.4, 3.75)
ORBIT_RADII = (1.35, 3.15)
ORBIT_SQUASH = 0.66
CENTRE_RADIUS = 0.42
# A branch: the room between two levels and between two leaves, the room left of the first
# column and right of the last for names, and the least height.
COLUMN = 2.6
SLOT = 0.62
BRANCH_LEFT = 1.4
BRANCH_NAMES = 2.9
BRANCH_HEIGHT = 6.4
BRANCH_TOP = 1.9
BRANCH_BOTTOM = 1.1


@dataclass(frozen=True, slots=True)
class Star:
    value: int
    name: str
    rank: int
    weight: float
    x: float
    y: float
    # 0 for a Value of weight 0: a dim star.
    radius: float
    flagged: bool


@dataclass(frozen=True, slots=True)
class Node:
    item: Item
    title: str
    # goal, subgoal, action or check.
    kind: str
    x: float
    y: float
    # The orbit or column it stands on; a Goal above a branch counts back from it.
    level: int
    # The Values it carries itself.
    values: tuple[int, ...]
    moving: bool
    repeats: bool
    blocked: bool
    # A Goal's share of the one-off Actions under it that are Done.
    progress: float | None
    flagged: bool
    # A Goal above a branch, drawn dashed.
    ghost: bool = False


@dataclass(frozen=True, slots=True)
class Picture:
    # sky, orbits or branch.
    kind: str
    # What the chat calls it, and what heads the picture with the line under it.
    title: str
    heading: str
    subtitle: str
    width: float
    height: float
    # Each Value's place among them all by name, so it keeps its colour in every picture.
    palette: dict[int, int]
    panel: Panel
    stars: tuple[Star, ...] = ()
    nodes: tuple[Node, ...] = ()
    # Two Values and the weight that counts for both.
    shared: tuple[tuple[int, int, float], ...] = ()
    # Each item that carries the orbits' Value itself, and the weight its line brings.
    rays: tuple[tuple[Item, float], ...] = ()
    # Each parent and its child, and each Card and its Check.
    branches: tuple[tuple[Item, Item], ...] = ()
    orbits: tuple[float, ...] = ()
    # Where the message under the picture leads: "value" or "goal", its id and its name.
    doors: tuple[tuple[str, int, str], ...] = ()


async def read_sky(session: AsyncSession, now: datetime) -> Picture | None:
    """Every Value as a star; None with no Value and no open Goal or Action."""
    weighed = await weigh(session, now)
    tree = weighed.tree
    if not tree.values and not tree.cards:
        return None
    flagged = {finding.item for finding in plain_findings(tree)}
    weights = {value.id: weighed.of(value.id) for value in tree.values}
    heaviest = max(weights.values(), default=0.0)
    count = len(tree.values)
    step = max(ARC_STEP, ARC_SPAN / (count - 1)) if count > 1 else 0.0
    stars = []
    for index, value in enumerate(tree.values):
        along = index / (count - 1) if count > 1 else 0.5
        weight = weights[value.id]
        stars.append(Star(
            value=value.id,
            name=value.name,
            rank=index + 1,
            weight=weight,
            x=ARC_LEFT + (index * step if count > 1 else ARC_SPAN / 2),
            y=ARC_BASE + ARC_RISE * math.sin(math.pi * along) + ARC_ZIGZAG * (-1) ** index,
            radius=max(STAR_RADIUS[0], STAR_RADIUS[1] * math.sqrt(weight / heaviest))
            if weight else 0.0,
            flagged=("value", value.id) in flagged,
        ))
    shared = tuple(
        (first, second, together)
        for first, second in combinations(weights, 2)
        if (together := weighed.shared(first, second))
    )
    today = now.astimezone(await workspace_zone(session)).date()
    kinds = [card.kind for card in tree.cards]
    right = max((star.x for star in stars), default=ARC_LEFT + ARC_SPAN) + 1.6
    return Picture(
        kind="sky",
        title="Constellation",
        heading="Constellation",
        subtitle=" · ".join((
            f"{day_month(today)} {today.year}",
            _counted(count, "Value"),
            _counted(kinds.count(CardKind.GOAL.value), "open Goal"),
            _counted(kinds.count(CardKind.ACTION.value), "open Action"),
        )),
        width=max(right, ARC_LEFT + ARC_SPAN + 1.6) + PANEL_WIDTH + 2 * PANEL_MARGIN,
        height=SKY_HEIGHT,
        palette=_palette(tree),
        panel=sky_panel(weighed, await read_history(session, now)),
        stars=tuple(stars),
        shared=shared,
        doors=tuple(("value", star.value, star.name) for star in stars if star.weight),
    )


async def read_orbits(session: AsyncSession, value_id: int, now: datetime) -> Picture | None:
    """One Value with what counts for it around it; None when there is no such Value."""
    weighed = await weigh(session, now)
    tree = weighed.tree
    names = {value.id: value.name for value in tree.values}
    if value_id not in names:
        return None
    entities = _entities(tree)
    inside = sorted(item for item, ids in weighed.counts.items() if value_id in ids)
    above = {item: _above(item, tree, set(inside)) for item in inside}
    children: dict[Item, list[Item]] = {}
    for item in inside:
        for parent in above[item]:
            children.setdefault(parent, []).append(item)
    roots = sorted(
        (item for item in inside if not above[item]),
        key=lambda item: (-_reach(item, children, weighed), item),
    )
    # Each item belongs to the first item above it that reaches it, and stands in its sector.
    owned: dict[Item, list[Item]] = {}
    depth: dict[Item, int] = {}

    def adopt(item: Item, level: int) -> None:
        depth[item] = level
        for child in sorted(children.get(item, []), key=lambda each: (_rank(entities[each]), each)):
            if child not in depth:
                owned.setdefault(item, []).append(child)
                adopt(child, level + 1)

    for root in roots:
        adopt(root, 0)
    goal_rings = 1 + max(
        (depth[item] for item in inside if _rank(entities[item]) == 0), default=-1
    )
    rings = goal_rings + any(_rank(entities[item]) for item in inside)
    radii = tuple(
        ORBIT_RADII[0] + (ORBIT_RADII[1] - ORBIT_RADII[0]) * index / max(rings - 1, 1)
        for index in range(rings)
    ) if rings > 1 else ((ORBIT_RADII[1] + ORBIT_RADII[0]) / 2,) * rings

    def leaves(item: Item) -> int:
        return max(1, sum(leaves(child) for child in owned.get(item, [])))

    angle: dict[Item, float] = {}

    def spread(item: Item, start: float, end: float) -> None:
        angle[item] = (start + end) / 2
        total = sum(leaves(child) for child in owned.get(item, []))
        for child in owned.get(item, []):
            share = (end - start) * leaves(child) / total
            spread(child, start, start + share)
            start += share

    every = sum(leaves(root) for root in roots)
    start = math.radians(95)
    gap = math.radians(6) if len(roots) > 1 else 0.0
    for root in roots:
        share = 2 * math.pi * leaves(root) / every
        spread(root, start - share + gap / 2, start - gap / 2)
        start -= share

    def brings(item: Item) -> float:
        return weighed.weight[item] + sum(
            brings(child) for child in owned.get(item, [])
            if value_id not in weighed.own[child]
        )

    rays = tuple(
        (item, brings(item)) for item in inside if value_id in weighed.own[item]
    )
    flagged = {finding.item for finding in plain_findings(tree)}
    centre_x, centre_y = ORBITS_CENTRE
    nodes = []
    for item in inside:
        ring = depth[item] if _rank(entities[item]) == 0 else rings - 1
        x = centre_x + radii[ring] * math.cos(angle[item])
        y = centre_y + radii[ring] * ORBIT_SQUASH * math.sin(angle[item])
        nodes.append(_node(
            item, entities[item], tree, x, y, ring, flagged,
            repeats=item in weighed.runs, exclude=value_id,
        ))
    weights = {value.id: weighed.of(value.id) for value in tree.values}
    weight = weights[value_id]
    rank = [value.id for value in tree.values].index(value_id) + 1
    place = 1 + sum(other > weight for other in weights.values())
    return Picture(
        kind="orbits",
        title=f"Constellation · {names[value_id]}",
        heading=names[value_id],
        subtitle=f"Rank {rank} · weight {weight:.0f} · {_ordinal(place)} of {len(weights)} by weight",
        width=ORBITS_SIZE[0],
        height=ORBITS_SIZE[1],
        palette=_palette(tree),
        panel=value_panel(weighed, await read_history(session, now), value_id),
        stars=(Star(
            value=value_id, name=names[value_id], rank=rank, weight=weight,
            x=centre_x, y=centre_y, radius=CENTRE_RADIUS if weight else 0.0,
            flagged=("value", value_id) in flagged,
        ),),
        nodes=tuple(nodes),
        rays=rays,
        branches=tuple((parent, item) for item in inside for parent in above[item]),
        orbits=radii,
        doors=tuple(
            ("goal", node.item[1], node.title)
            for node in nodes if node.level == 0 and node.kind in ("goal", "subgoal")
        ),
    )


async def read_branch(session: AsyncSession, goal_id: int, now: datetime) -> Picture | None:
    """One open Goal's open branch; None when it is not an open Goal."""
    tree = await read_tree(session)
    entities = _entities(tree)
    cards = {card.id: card for card in tree.cards}
    goal = cards.get(goal_id)
    if goal is None or goal.kind != CardKind.GOAL.value:
        return None
    inside: set[Item] = set()
    waiting: list[Item] = [("card", goal_id)]
    under: dict[Item, list[Item]] = {}
    for card in tree.cards:
        for parent in tree.card_parents.get(card.id, ()):
            if parent in cards:
                under.setdefault(("card", parent), []).append(("card", card.id))
    for check in tree.checks:
        on = tree.check_cards.get(check.id)
        if on is not None and on.id in cards:
            under.setdefault(("card", on.id), []).append(("check", check.id))
    while waiting:
        current = waiting.pop()
        if current not in inside:
            inside.add(current)
            waiting.extend(under.get(current, []))

    # Each item stands once, beside the first parent here that reaches it; leaves take a
    # row each from the top, and a parent stands beside the middle of its children.
    column: dict[Item, int] = {}
    row: dict[Item, float] = {}
    slot = 0

    def place(item: Item, level: int) -> None:
        nonlocal slot
        column[item] = level
        mine = [
            child for child in sorted(under.get(item, []), key=lambda each: (_rank(entities[each]), each))
            if child not in column
        ]
        for child in mine:
            place(child, level + 1)
        if mine:
            row[item] = sum(row[child] for child in mine) / len(mine)
        else:
            row[item] = slot
            slot += 1

    place(("card", goal_id), 0)

    # The Goals above it, each as far left as its fewest steps up.
    parents = await parents_of(session)
    steps: dict[int, int] = {}
    frontier = [goal_id]
    while frontier:
        reached = []
        for card_id in frontier:
            for parent in parents.get(card_id, ()):
                if parent not in steps:
                    steps[parent] = (steps.get(card_id, 0)) + 1
                    reached.append(parent)
        frontier = reached
    ghosts = {
        card.id: card
        for card in await session.scalars(select(Card).where(Card.id.in_(list(steps))))
    }
    for distance in sorted(set(steps.values())):
        level = sorted(card_id for card_id, steps_up in steps.items() if steps_up == distance)
        for index, card_id in enumerate(level):
            column[("card", card_id)] = -distance
            row[("card", card_id)] = row[("card", goal_id)] + (index - (len(level) - 1) / 2) * 1.6

    top_row = min(row.values())
    span = (max(row.values()) - top_row) * SLOT
    height = max(BRANCH_HEIGHT, span + BRANCH_TOP + BRANCH_BOTTOM)
    # A small branch stands in the middle of the room under the heading.
    top = height - BRANCH_TOP - (height - BRANCH_TOP - BRANCH_BOTTOM - span) / 2
    left = -min(column.values()) * COLUMN + BRANCH_LEFT

    def at(item: Item) -> tuple[float, float]:
        return left + column[item] * COLUMN, top - (row[item] - top_row) * SLOT

    children_all, below = await read_below(session, goal_id)
    below[goal_id] = goal
    flagged = {finding.item for finding in plain_findings(tree)}
    tz = await workspace_zone(session)
    nodes = []
    for item in sorted(inside):
        x, y = at(item)
        progress = (
            one_off_share(actions_under(item[1], children_all, below))
            if item[0] == "card" and cards[item[1]].kind == CardKind.GOAL.value else None
        )
        entity = entities[item]
        nodes.append(_node(
            item, entity, tree, x, y, column[item], flagged, progress=progress,
            repeats=_rank(entity) > 0 and repeating(entity),
        ))
    for card_id, card in ghosts.items():
        x, y = at(("card", card_id))
        nodes.append(Node(
            item=("card", card_id), title=card.title,
            kind="subgoal" if parents.get(card_id) else "goal", x=x, y=y,
            level=column[("card", card_id)], values=(), moving=False, repeats=False,
            blocked=False, progress=None, flagged=False, ghost=True,
        ))
    branches = [
        (parent, item) for parent, items in under.items() if parent in inside
        for item in items if item in inside
    ]
    branches += [
        (("card", parent), ("card", card_id))
        for card_id in (goal_id, *ghosts) for parent in parents.get(card_id, ())
        if parent in ghosts
    ]
    today = now.astimezone(tz).date()
    kinds = [node.kind for node in nodes if not node.ghost]
    right = max(x for x, _ in map(at, column)) + BRANCH_NAMES
    return Picture(
        kind="branch",
        title=f"Constellation · {goal.title}",
        heading=goal.title,
        subtitle=" · ".join((
            f"{day_month(today)} {today.year}",
            _counted(kinds.count("goal") + kinds.count("subgoal"), "Goal"),
            _counted(kinds.count("action"), "Action"),
            _counted(kinds.count("check"), "Check"),
        )),
        width=right + PANEL_WIDTH + 2 * PANEL_MARGIN,
        height=height,
        palette=_palette(tree),
        panel=goal_panel(
            goal, actions_under(goal_id, children_all, below), today, tz,
            effort=await effort_tracking_on(session), time=await time_tracking_on(session),
        ),
        nodes=tuple(nodes),
        branches=tuple(branches),
    )


def _palette(tree: Tree) -> dict[int, int]:
    return {
        each.id: index
        for index, each in enumerate(sorted(tree.values, key=lambda value: value.name.casefold()))
    }


def _above(item: Item, tree: Tree, inside: set[Item]) -> list[Item]:
    """The items here directly above this one: a Card's parents, a Check's Card."""
    if item[0] == "card":
        return [
            ("card", parent) for parent in tree.card_parents.get(item[1], ())
            if ("card", parent) in inside
        ]
    on = tree.check_cards.get(item[1])
    return [("card", on.id)] if on is not None and ("card", on.id) in inside else []


def _reach(item: Item, children: dict[Item, list[Item]], weighed: Weighed) -> float:
    """The weight under an item, itself included, each item once."""
    seen: set[Item] = set()
    waiting = [item]
    while waiting:
        current = waiting.pop()
        if current not in seen:
            seen.add(current)
            waiting.extend(children.get(current, []))
    return sum(weighed.weight[each] for each in seen)


def _entities(tree: Tree) -> dict[Item, Card | Check]:
    return {("card", card.id): card for card in tree.cards} | {
        ("check", check.id): check for check in tree.checks
    }


def _rank(entity: Card | Check) -> int:
    """Goals before Actions before Checks among one parent's children."""
    if isinstance(entity, Check):
        return 2
    return 0 if entity.kind == CardKind.GOAL.value else 1


def _node(
    item: Item,
    entity: Card | Check,
    tree: Tree,
    x: float,
    y: float,
    level: int,
    flagged: set[tuple[str, int]],
    *,
    repeats: bool,
    exclude: int | None = None,
    progress: float | None = None,
) -> Node:
    if isinstance(entity, Card):
        values = tree.card_values.get(entity.id, [])
        kind = entity.kind
        if kind == CardKind.GOAL.value:
            kind = "subgoal" if tree.card_parents.get(entity.id) else "goal"
        moving, blocked = entity.effective_stage in MOVING, entity.blocked
    else:
        values = tree.check_values.get(entity.id, [])
        kind, moving, blocked = "check", False, False
    return Node(
        item=item,
        title=entity.title,
        kind=kind,
        x=x,
        y=y,
        level=level,
        values=tuple(each for each in values if each != exclude),
        moving=moving,
        repeats=repeats,
        blocked=blocked,
        progress=progress,
        flagged=item in flagged,
    )


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"
