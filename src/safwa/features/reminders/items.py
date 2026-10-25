"""What the reminder subagent and the condition's own session are handed: open items and
Reminders, the closest to the end of the conversation first.

The subagent reads no view. It is handed the open items with a Schedule, which `remind`
takes; the open ones without, which its `remind_text` names; and the owner's Reminders, which
a change or a deletion names. The condition's own session is handed more of the ones with a
Schedule, the only ones a Reminder on answers counts. Both read them after their
conversation and pick the owner's by meaning, in any language, which a search cannot do for
a short title such as «Meditated?»: its words share no stem with the owner's. The end of
that conversation orders them by words and by meaning, so the cut keeps what the owner is
talking about. Each is named as `remind` and the condition take it, 'Check #12
«Meditated?»', with the Schedule, Tags and Values that a group is told by.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import Ranking
from tg_agent_shell.telegram.manifest import AgentContext

from ...foundation.tokens import TOKEN_CHARS_ESTIMATE
from ..cards.model import Card, CardKind
from ..checks.model import Check
from ..schedules.api import item_type
from ..tags.model import CardTag, Tag
from ..values.model import CardValue, CheckValue, Value
from .api import describe, lead_words, schedule_of
from .model import Reminder
from .views import AI_REMINDERS

# How many items each block of the subagent lists, and the condition's session's one.
SUBAGENT_ITEMS_MAX = 10
OPEN_ITEMS_MAX = 50
# How much of the conversation's end orders them; the text model reads up to 2048 tokens.
RANKED_BY_TOKENS = 512
OPEN_ITEMS = "Open items:"
NO_OPEN_ITEMS = "Open items: none."
SCHEDULED = "Open items with a Schedule, for `remind`:"
UNSCHEDULED = "Open items without a Schedule:"
REMINDERS = "Reminders:"
_ITEM_VIEWS = ("ai_checks", "ai_cards")
# A Check or an Action as a line of the block begins.
_LISTED = re.compile(r"^- (Check|Action) #(\d+) ", re.MULTILINE)
# Who said each line of the conversation, which is no word of what it is about.
_TAG = re.compile(r"</?[A-Za-z]+>")

Key = tuple[str, int]


async def _names(session: AsyncSession, query: Any) -> dict[int, list[str]]:
    """The names a query pairs with each item id, in its order."""
    names: dict[int, list[str]] = defaultdict(list)
    for item_id, name in await session.execute(query):
        names[item_id].append(name)
    return names


def _line(
    name: str, schedule: str | None, tags: list[str], values: list[str], lead: int | None = None
) -> str:
    parts = [name] if schedule is None else [name, schedule]
    if lead is not None:
        parts.append("remind: " + (f"{lead_words(lead)} before" if lead else "at its time"))
    if tags:
        parts.append("tags: " + ", ".join(tags))
    if values:
        parts.append("values: " + ", ".join(values))
    return "- " + "; ".join(parts)


async def _items(
    session: AsyncSession, *, scheduled: bool, leads: Mapping[Key, int] | None = None
) -> list[tuple[Key, str]]:
    """Open Checks, then open Actions and Goals, with a Schedule or without one; a Goal's is
    its deadline. Only an answered Check or a Done Card is archived, so none of them is. An
    item in `leads` has Remind on, that many minutes before its time."""
    leads = leads or {}
    check_has = Check.schedule.is_not(None) & (Check.schedule != "")
    card_has = Card.schedule.is_not(None) & (Card.schedule != "")
    if not scheduled:
        check_has = or_(Check.schedule.is_(None), Check.schedule == "")
        card_has = or_(Card.schedule.is_(None), Card.schedule == "")
    checks = list(
        await session.scalars(select(Check).where(Check.outcome.is_(None), check_has).order_by(Check.id))
    )
    cards = list(
        await session.scalars(
            select(Card).where(Card.completed_at.is_(None), card_has).order_by(Card.id)
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
    return [
        (
            (item_type(Check), check.id),
            _line(
                f"Check #{check.id} «{check.title}»",
                f"schedule: {check.schedule}" if scheduled else None,
                [],
                check_values[check.id],
                leads.get((item_type(Check), check.id)),
            ),
        )
        for check in checks
    ] + [
        (
            (item_type(Card), card.id),
            _line(
                f"{card.kind.title()} #{card.id} «{card.title}»",
                (
                    ("deadline" if card.kind == CardKind.GOAL.value else "schedule")
                    + f": {card.schedule}"
                )
                if scheduled
                else None,
                card_tags[card.id],
                card_values[card.id],
                leads.get((item_type(Card), card.id)),
            ),
        )
        for card in cards
    ]


async def _reminders(session: AsyncSession, tz: ZoneInfo) -> list[tuple[Key, str]]:
    """The owner's own Reminders, by their words and when they fire. One an item's Remind
    made is that item's to change."""
    now = datetime.now(UTC)
    reminders = await session.scalars(
        select(Reminder).where(Reminder.item_type.is_(None)).order_by(Reminder.id)
    )
    return [
        (
            ("reminder", reminder.id),
            f"- Reminder #{reminder.id} «{' '.join(reminder.remind_text.split())}»; "
            f"{describe(schedule_of(reminder), tz=tz, now=now)}",
        )
        for reminder in reminders
    ]


async def _closeness(
    ranking: Ranking | None, conversation: str, views: Collection[str]
) -> Mapping[Key, float]:
    """How close each item is to the conversation's newest RANKED_BY_TOKENS tokens."""
    words = _TAG.sub(" ", conversation)[-int(RANKED_BY_TOKENS * TOKEN_CHARS_ESTIMATE) :].strip()
    if ranking is None or not words:
        return {}
    return await ranking.relevance(words, views)


async def _leads(session: AsyncSession) -> dict[Key, int]:
    """The items with Remind on, and how many minutes before their time it fires."""
    rows = await session.execute(
        select(Reminder.item_type, Reminder.item_id, Reminder.lead_minutes).where(
            Reminder.item_type.is_not(None)
        )
    )
    return {(kind, item_id): lead for kind, item_id, lead in rows}


def _closest(lines: list[tuple[Key, str]], closeness: Mapping[Key, float], most: int) -> list[str]:
    """The `most` lines, the closest first and the rest in their own order after."""
    ordered = sorted(lines, key=lambda line: (line[0] not in closeness, -closeness.get(line[0], 0.0)))
    return [line for _key, line in ordered[:most]]


def _block(header: str, lines: list[str]) -> str:
    return "\n".join([header, *lines]) if lines else f"{header} none."


async def open_items(
    session: AsyncSession, ranking: Ranking | None, conversation: str
) -> str:
    """The condition's own session's block: up to OPEN_ITEMS_MAX open items with a Schedule,
    the closest to the `conversation` first."""
    lines = await _items(session, scheduled=True)
    if not lines:
        return NO_OPEN_ITEMS
    closeness = await _closeness(ranking, conversation, _ITEM_VIEWS)
    return "\n".join([OPEN_ITEMS, *_closest(lines, closeness, OPEN_ITEMS_MAX)])


def listed_items(block: str) -> set[tuple[str, int]]:
    """The Checks and Actions an `open_items` block lists, as check or action and #id."""
    return {(kind.lower(), int(item_id)) for kind, item_id in _LISTED.findall(block)}


async def reminder_items(context: AgentContext, conversation: str) -> str:
    """The subagent's blocks: up to SUBAGENT_ITEMS_MAX open items with a Schedule, with their
    Remind, and as many without, then the owner's Reminders, each the closest to its
    `conversation` first."""
    closeness = await _closeness(
        context.query_runner.search, conversation, (*_ITEM_VIEWS, AI_REMINDERS.name)
    )
    async with context.sessions() as session:
        scheduled = await _items(session, scheduled=True, leads=await _leads(session))
        unscheduled = await _items(session, scheduled=False)
        reminders = await _reminders(session, ZoneInfo(context.timezone))
    return "\n\n".join(
        [
            _block(SCHEDULED, _closest(scheduled, closeness, SUBAGENT_ITEMS_MAX)),
            _block(UNSCHEDULED, _closest(unscheduled, closeness, SUBAGENT_ITEMS_MAX)),
            _block(REMINDERS, _closest(reminders, closeness, len(reminders))),
        ]
    )
