"""The items the reminder subagent may Remind of, and a Reminder on answers may count: the
open ones with a Schedule.

They follow the subagent's dialogue as its current values, and the condition's own session
reads them after its conversation, so both pick the owner's items by meaning, in any
language, which `search` cannot do for a short title such as «Meditated?»: its words share
no stem with the owner's. Each is named as `remind` and the condition take it,
'Check #12 «Meditated?»', with the Schedule, Tags and Values that a group is told by.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.telegram.manifest import AgentContext

from ..cards.model import Card, CardKind
from ..checks.model import Check
from ..tags.model import CardTag, Tag
from ..values.model import CardValue, CheckValue, Value

# How many items the block lists before it sends the rest to `search`.
OPEN_ITEMS_MAX = 50
OPEN_ITEMS = "Open items:"
NO_OPEN_ITEMS = "Open items: none."
# A Check or an Action as a line of the block begins.
_LISTED = re.compile(r"^- (Check|Action) #(\d+) ", re.MULTILINE)


async def _names(session: AsyncSession, query: Any) -> dict[int, list[str]]:
    """The names a query pairs with each item id, in its order."""
    names: dict[int, list[str]] = defaultdict(list)
    for item_id, name in await session.execute(query):
        names[item_id].append(name)
    return names


def _line(name: str, schedule: str, tags: list[str], values: list[str]) -> str:
    parts = [name, schedule]
    if tags:
        parts.append("tags: " + ", ".join(tags))
    if values:
        parts.append("values: " + ", ".join(values))
    return "- " + "; ".join(parts)


async def open_items(session: AsyncSession) -> str:
    """Open Checks, then open Actions and Goals, each with a Schedule; a Goal's is its
    deadline. Only an answered Check or a Done Card is archived, so none of them is."""
    checks = list(
        await session.scalars(
            select(Check)
            .where(Check.outcome.is_(None), Check.schedule.is_not(None), Check.schedule != "")
            .order_by(Check.id)
        )
    )
    cards = list(
        await session.scalars(
            select(Card)
            .where(Card.completed_at.is_(None), Card.schedule.is_not(None), Card.schedule != "")
            .order_by(Card.id)
        )
    )
    card_ids = [card.id for card in cards]
    check_values = await _names(
        session,
        select(CheckValue.check_id, Value.name)
        .join(Value, Value.id == CheckValue.value_id)
        .where(CheckValue.check_id.in_([check.id for check in checks]))
        .order_by(Value.order),
    )
    card_values = await _names(
        session,
        select(CardValue.card_id, Value.name)
        .join(Value, Value.id == CardValue.value_id)
        .where(CardValue.card_id.in_(card_ids))
        .order_by(Value.order),
    )
    card_tags = await _names(
        session,
        select(CardTag.card_id, Tag.name)
        .join(Tag, Tag.id == CardTag.tag_id)
        .where(CardTag.card_id.in_(card_ids))
        .order_by(Tag.name),
    )
    lines = [
        _line(
            f"Check #{check.id} «{check.title}»",
            f"schedule: {check.schedule}",
            [],
            check_values[check.id],
        )
        for check in checks
    ] + [
        _line(
            f"{card.kind.title()} #{card.id} «{card.title}»",
            ("deadline" if card.kind == CardKind.GOAL.value else "schedule") + f": {card.schedule}",
            card_tags[card.id],
            card_values[card.id],
        )
        for card in cards
    ]
    if not lines:
        return NO_OPEN_ITEMS
    shown = lines[:OPEN_ITEMS_MAX]
    if len(lines) > OPEN_ITEMS_MAX:
        shown.append(f"And {len(lines) - OPEN_ITEMS_MAX} more: find them with `search`.")
    return "\n".join([OPEN_ITEMS, *shown])


def listed_items(block: str) -> set[tuple[str, int]]:
    """The Checks and Actions an `open_items` block lists, as check or action and #id."""
    return {(kind.lower(), int(item_id)) for kind, item_id in _LISTED.findall(block)}


async def reminder_items(context: AgentContext) -> str:
    async with context.sessions() as session:
        return await open_items(session)
