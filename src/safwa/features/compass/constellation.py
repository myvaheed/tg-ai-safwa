"""What the Constellation shows, and where: the part of the tree asked for, with the Values in
the sky above, each Goal above its children and the Actions at the bottom — up is why, down
is how.

A Goal stands as high as its `tree_level`, the number `ai_cards` gives it. The branches are
laid out a leaf at a time, each parent over the middle of its children, and a root's branch
goes beside the others that serve its Value most. Each Value then stands over what carries it,
moved apart only as far as its name needs.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from ..cards.model import TERMINAL_STAGES, Card, CardKind
from .graph import plain_findings, read_tree

# The picture's own units: the height of one level of the tree, and the sky's above the highest.
ROW = 1.6
SKY_ABOVE = 2.6
# Where a Check on a Card stands from it, and from the Check before it on that Card.
CHECK_OFFSET = (0.3, -0.5)
CHECK_STEP = 0.35
# The room between two roots' branches, the least between two Values, and how much of a row
# one character of a Value's name takes: the picture writes it at 14 pt, 0.85 inch a unit.
ROOT_GAP = 0.8
VALUE_GAP = 2.4
NAME_UNITS = 0.13

Item = tuple[str, int]


class Detail(StrEnum):
    GOALS = "goals"
    ACTIONS = "actions"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class Star:
    """One drawn item where it stands: a Value, a Goal, a Subgoal, an Action or a Check."""

    item: Item
    title: str
    kind: str
    x: float
    y: float
    # How many levels above the Actions a Goal stands; 0 for the rest.
    level: int
    # The Value whose colour it takes: its own first, else its parent's.
    hue: int | None
    done: bool
    flagged: bool


@dataclass(frozen=True, slots=True)
class Sky:
    title: str
    stars: tuple[Star, ...]
    # Each parent and its child, a Card and its Check.
    branches: tuple[tuple[Item, Item], ...]
    # Each Value and an item that carries it.
    links: tuple[tuple[int, Item], ...]
    width: float
    # Where the Values stand.
    top: float
    # Each Value's place among them all by name, so it keeps its colour in every picture.
    palette: dict[int, int]


async def read_sky(
    session: AsyncSession,
    *,
    value: int | None = None,
    goal: int | None = None,
    detail: Detail = Detail.ACTIONS,
    done: bool = False,
) -> Sky | None:
    """The sky of the whole tree, of one Value or of one Goal's branch; None when there is
    nothing to draw."""
    tree = await read_tree(session, done=done)
    flagged = {
        finding.item
        for finding in plain_findings(await read_tree(session) if done else tree)
    }
    palette = {each.id: index for index, each in enumerate(tree.values)}
    names = {each.id: each.name for each in tree.values}
    cards = {
        card.id: card
        for card in tree.cards
        if detail is not Detail.GOALS or card.kind == CardKind.GOAL.value
    }
    children = _children(cards)
    if goal is not None:
        keep = set(_below(goal, children)) if goal in cards else set()
    elif value is not None:
        keep = set()
        for card_id in cards:
            if value in tree.card_values.get(card_id, []):
                keep |= set(_above(card_id, cards)) | set(_below(card_id, children))
    else:
        keep = set(cards)
    cards = {card_id: card for card_id, card in cards.items() if card_id in keep}
    children = _children(cards)

    checks = []
    if detail is Detail.ALL:
        for check in tree.checks:
            card = tree.check_cards.get(check.id)
            on = card.id if card is not None and card.id in cards else None
            carries = value in tree.check_values.get(check.id, [])
            if on is not None or (goal is None and (value is None or carries)):
                checks.append((check, on))

    carried: dict[Item, list[int]] = {
        ("card", card_id): tree.card_values.get(card_id, []) for card_id in cards
    }
    carried |= {("check", check.id): tree.check_values.get(check.id, []) for check, _ in checks}
    if value is None and goal is None:
        shown = list(palette)
    else:
        shown = [
            each for each in palette
            if each == value or any(each in ids for ids in carried.values())
        ]
    if not shown and not cards and not checks:
        return None

    levels: dict[int, int] = {}

    def level(card_id: int) -> int:
        if card_id not in levels:
            below = [
                level(child) for child in children.get(card_id, [])
                if cards[child].kind == CardKind.GOAL.value
            ]
            levels[card_id] = (
                1 + max(below, default=0) if cards[card_id].kind == CardKind.GOAL.value else 0
            )
        return levels[card_id]

    def primary(root: int) -> int | None:
        counts = Counter(
            each for card_id in _below(root, children)
            for each in carried[("card", card_id)] if each in shown
        )
        return min(counts, key=lambda each: (-counts[each], palette[each])) if counts else None

    def order(root: int) -> tuple[bool, int, int]:
        first = primary(root)
        return (first is None, palette[first] if first is not None else 0, root)

    roots = sorted(children.get(None, []), key=order)
    x: dict[Item, float] = {}
    slot = 0.0

    def place(card_id: int) -> None:
        nonlocal slot
        under = children.get(card_id, [])
        if not under:
            x[("card", card_id)] = slot
            slot += 1
            return
        for child in under:
            place(child)
        x[("card", card_id)] = sum(x[("card", child)] for child in under) / len(under)

    for root in roots:
        place(root)
        slot += ROOT_GAP
    y: dict[Item, float] = {("card", card_id): level(card_id) * ROW for card_id in cards}
    stacked: Counter[int] = Counter()
    for check, on in checks:
        if on is None:
            x[("check", check.id)], y[("check", check.id)] = slot, 0.0
            slot += 1
        else:
            x[("check", check.id)] = x[("card", on)] + CHECK_OFFSET[0]
            y[("check", check.id)] = y[("card", on)] + CHECK_OFFSET[1] - CHECK_STEP * stacked[on]
            stacked[on] += 1
    width = max(slot, 1.0)
    top = max(levels.values(), default=0) * ROW + SKY_ABOVE

    links = tuple(
        (each, item) for item, ids in carried.items() for each in ids if each in shown
    )
    above = _spread(
        {
            each: [x[item] for linked, item in links if linked == each]
            for each in shown
        },
        {each: names[each] for each in shown},
        palette,
        width,
    )
    width = max(width, max(above.values(), default=0.0) + 1)

    hues: dict[int, int | None] = {}

    def hue(card_id: int) -> int | None:
        if card_id not in hues:
            own = [each for each in carried[("card", card_id)] if each in shown]
            parent = cards[card_id].parent_id
            hues[card_id] = own[0] if own else (hue(parent) if parent in cards else None)
        return hues[card_id]

    finished = {stage.value for stage in TERMINAL_STAGES}
    stars = [
        Star(
            item=("value", each), title=names[each], kind="value", x=above[each], y=top,
            level=0, hue=each, done=False, flagged=("value", each) in flagged,
        )
        for each in shown
    ]
    stars += [
        Star(
            item=("card", card.id),
            title=card.title,
            kind="subgoal" if card.kind == CardKind.GOAL.value and card.parent_id in cards
            else card.kind,
            x=x[("card", card.id)],
            y=y[("card", card.id)],
            level=levels[card.id],
            hue=hue(card.id),
            done=card.effective_stage in finished,
            flagged=("card", card.id) in flagged,
        )
        for card in cards.values()
    ]
    for check, on in checks:
        own = [each for each in carried[("check", check.id)] if each in shown]
        stars.append(Star(
            item=("check", check.id), title=check.title, kind="check",
            x=x[("check", check.id)], y=y[("check", check.id)], level=0,
            hue=own[0] if own else (hue(on) if on is not None else None),
            done=False, flagged=False,
        ))
    branches = tuple(
        (("card", card.parent_id), ("card", card.id))
        for card in cards.values() if card.parent_id in cards
    ) + tuple((("card", on), ("check", check.id)) for check, on in checks if on is not None)
    if goal is not None and goal in cards:
        title = f"Constellation · {cards[goal].title}"
    elif value is not None and value in names:
        title = f"Constellation · {names[value]}"
    else:
        title = "Constellation"
    return Sky(title, tuple(stars), branches, links, width, top, palette)


def _children(cards: dict[int, Card]) -> dict[int | None, list[int]]:
    """Each Card's children among these; a Card whose parent is not here is a root."""
    children: dict[int | None, list[int]] = {}
    for card in cards.values():
        children.setdefault(card.parent_id if card.parent_id in cards else None, []).append(card.id)
    return children


def _below(card_id: int, children: dict[int | None, list[int]]) -> Iterator[int]:
    yield card_id
    for child in children.get(card_id, []):
        yield from _below(child, children)


def _above(card_id: int, cards: dict[int, Card]) -> Iterator[int]:
    while card_id in cards:
        yield card_id
        card_id = cards[card_id].parent_id


def _spread(
    under: dict[int, list[float]], names: dict[int, str], palette: dict[int, int], width: float
) -> dict[int, float]:
    """Each Value over the middle of what carries it, as far from the one before as both
    names need, and at least `VALUE_GAP`; one that nothing carries goes to the right."""
    wanted = {
        each: sum(xs) / len(xs) if xs else width + VALUE_GAP for each, xs in under.items()
    }

    def half(each: int) -> float:
        return max(VALUE_GAP, len(names[each]) * NAME_UNITS + 0.6) / 2

    placed: dict[int, float] = {}
    previous: int | None = None
    for each in sorted(wanted, key=lambda each: (wanted[each], palette[each])):
        placed[each] = wanted[each] if previous is None else max(
            wanted[each], placed[previous] + half(previous) + half(each)
        )
        previous = each
    if placed:
        # Moved apart, the row is shifted back to sit over its items as a whole.
        shift = sum(wanted[each] - placed[each] for each in placed) / len(placed)
        shift = max(shift, -min(placed.values()))
        placed = {each: at + shift for each, at in placed.items()}
    return placed
