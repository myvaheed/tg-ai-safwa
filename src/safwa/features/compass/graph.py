"""The whole tree as Compass reads it: what may be set better in it, what SQL finds there
without a model, and the tree written out for a model that runs no query."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...constants import INBOX_TAG_NAME
from ..cards.model import TERMINAL_STAGES, Card, CardCheck, CardKind, card_kind_name
from ..checks.model import Check
from ..tags.model import CardTag, Tag
from ..values.model import CardValue, CheckValue, Value

# A Goal carrying this many Values or more serves none of them clearly.
GOAL_VALUES_MANY = 4
# How many findings one answer names, the most important first.
COMPASS_FINDINGS_MAX = 3
# How much of a Note the written-out tree carries.
TREE_NOTE_MAX_CHARS = 200

PROBLEMS = (
    "A root Goal serves no Value.",
    f"A Goal carries {GOAL_VALUES_MANY} Values or more.",
    "A Value is on paper only: no open Goal, Action or Check carries it.",
    "A Value is really a Goal, a practice or a rule.",
    "A reversed link: the item needs the Value rather than making more of it.",
    "Two Values share one root: one is what the other is for, or both say the same.",
)
NOT_PROBLEMS = (
    "An Action carrying the same Value as its Goal is fine.",
    "Whether a Value, Goal or Action is right for the user is theirs to say.",
)


@dataclass(frozen=True, slots=True)
class Tree:
    """Every Value, and the open Cards and Checks with what they carry. A Card carrying the
    Inbox Tag is a capture and is left out."""

    values: list[Value]
    cards: list[Card]
    checks: list[Check]
    card_values: dict[int, list[int]]
    check_values: dict[int, list[int]]
    check_cards: dict[int, Card]


async def read_tree(session: AsyncSession) -> Tree:
    inbox = select(CardTag.card_id).join(Tag, Tag.id == CardTag.tag_id).where(
        Tag.name == INBOX_TAG_NAME
    )
    cards = list(await session.scalars(
        select(Card)
        .where(
            Card.archived_at.is_(None),
            Card.effective_stage.not_in([stage.value for stage in TERMINAL_STAGES]),
            Card.id.not_in(inbox),
        )
        .order_by(Card.id)
    ))
    checks = list(await session.scalars(
        select(Check)
        .where(Check.archived_at.is_(None), Check.outcome.is_(None))
        .order_by(Check.id)
    ))
    card_values: dict[int, list[int]] = {}
    for card_id, value_id in await session.execute(
        select(CardValue.card_id, CardValue.value_id)
        .where(CardValue.card_id.in_([card.id for card in cards]))
        .order_by(CardValue.value_id)
    ):
        card_values.setdefault(card_id, []).append(value_id)
    check_values: dict[int, list[int]] = {}
    for check_id, value_id in await session.execute(
        select(CheckValue.check_id, CheckValue.value_id)
        .where(CheckValue.check_id.in_([check.id for check in checks]))
        .order_by(CheckValue.value_id)
    ):
        check_values.setdefault(check_id, []).append(value_id)
    check_cards = {
        check_id: card
        for check_id, card in await session.execute(
            select(CardCheck.check_id, Card)
            .join(Card, Card.id == CardCheck.card_id)
            .where(CardCheck.check_id.in_([check.id for check in checks]))
        )
    }
    return Tree(
        values=list(await session.scalars(select(Value).order_by(Value.name))),
        cards=cards,
        checks=checks,
        card_values=card_values,
        check_values=check_values,
        check_cards=check_cards,
    )


def _card(card: Card) -> str:
    return f"[{card.title}](card:{card.id})"


def plain_findings(tree: Tree) -> list[str]:
    """What needs no model: Goals without a Value or with too many, and Values on paper."""
    found = []
    for card in tree.cards:
        if card.kind != CardKind.GOAL.value:
            continue
        carried = tree.card_values.get(card.id, [])
        if card.parent_id is None and not carried:
            found.append(f"{_card(card)}: a root Goal that serves no Value.")
        if len(carried) >= GOAL_VALUES_MANY:
            found.append(f"{_card(card)}: a Goal carrying {len(carried)} Values.")
    live = {
        value_id
        for carried in (*tree.card_values.values(), *tree.check_values.values())
        for value_id in carried
    }
    found += [
        f"[{value.name}](value:{value.id}): no open Goal, Action or Check carries it."
        for value in tree.values
        if value.id not in live
    ]
    return found


def tree_text(tree: Tree) -> str:
    """The tree for a model with no query to run: each Card under its parent."""
    names = {value.id: value.name for value in tree.values}

    def carried(ids: list[int]) -> str:
        return ", ".join(names[value_id] for value_id in ids)

    def note(text: str) -> str:
        text = " ".join(text.split())
        if len(text) > TREE_NOTE_MAX_CHARS:
            text = text[: TREE_NOTE_MAX_CHARS - 1].rstrip() + "…"
        return f" Note: {text}" if text else ""

    open_ids = {card.id for card in tree.cards}
    children: dict[int | None, list[Card]] = {}
    for card in tree.cards:
        parent = card.parent_id if card.parent_id in open_ids else None
        children.setdefault(parent, []).append(card)

    lines = ["Values:"]
    lines += [f"- [{value.name}](value:{value.id}): {value.description}" for value in tree.values]
    lines.append("Open Goals, Subgoals and Actions, each under its parent:")

    def walk(parent: int | None, depth: int) -> None:
        for card in children.get(parent, []):
            values = tree.card_values.get(card.id, [])
            serves = f" Values: {carried(values)}." if values else (
                " No Value." if card.kind == CardKind.GOAL.value else ""
            )
            lines.append(
                f"{'  ' * depth}- {_card(card)}, {card_kind_name(card.kind, card.parent_id)}."
                f"{serves}{note(card.note)}"
            )
            walk(card.id, depth + 1)

    walk(None, 0)
    lines.append("Open Checks:")
    for check in tree.checks:
        card = tree.check_cards.get(check.id)
        values = tree.check_values.get(check.id, [])
        lines.append(
            f"- [{check.title}](check:{check.id})"
            + (f", on {_card(card)}" if card else "")
            + (f". Values: {carried(values)}." if values else ".")
        )
    found = plain_findings(tree)
    lines.append("Found without a model:")
    lines += [f"- {line}" for line in found] or ["- nothing"]
    return "\n".join(lines)
