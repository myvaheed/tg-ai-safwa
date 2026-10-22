"""The Home dashboard: what a quiet chat is cleared down to, built from the workspace.

Five blocks under the day and the time, each read through the door of the feature that owns
it: the last changes, the Values in focus with the words written for them, all of Today under
its Goals, scheduled Checks remaining today, and the time tracked today. It is Markdown, as
an answer is, and every item is a citation, so it reads and links the way it does in an answer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow

from ...foundation.log_events import CREATE, DELETE, UPDATE, LogEvent
from ...foundation.workspace import require_workspace
from ..cards.api import effort_label, goal_tree, minutes_label, order_cards, tracked_between
from ..planning.api import plan_load, today_actions, values_in_focus
from ..profile.api import effort_tracking_on, time_tracking_on
from ..schedules.api import checks_today

# How many changes the dashboard opens with.
HOME_LOG_SHOWN = 5

_INDENT = "    "

_ICONS = {CREATE: "➕", DELETE: "🗑", "done": "✅"}
# What an operation is called on one short line; `edit_<field>` is its field.
_DONE_TO = {
    CREATE: "created",
    DELETE: "deleted",
    UPDATE: "updated",
    "move": "moved",
    "archive": "archived",
    "restore": "restored",
    "reschedule": "rescheduled",
}


_MARKDOWN = re.compile(r"([\\`*_~])")


def _plain(words: str, limit: int | None = None) -> str:
    """Words read as they are: no bracket a citation could take, no Markdown of their own."""
    flat = words.replace("[", "(").replace("]", ")").replace("\n", " ")[:limit]
    return _MARKDOWN.sub(r"\\\1", flat)


def _cite(item_type: str, item_id: int, words: str) -> str:
    """A citation that becomes a link, or its words when the item is gone."""
    return f"[{_plain(words, 60)}]({item_type}:{item_id})"


async def dashboard_text(
    session: AsyncSession,
    words: Mapping[int, str],
    *,
    now: datetime | None = None,
) -> str:
    """The dashboard as Markdown with citations, `words` under the Values they were written
    for."""
    tz = ZoneInfo((await require_workspace(session)).timezone)
    local = (now or utcnow()).astimezone(tz)
    blocks = (
        f"**🏠 {local:%a, %d %b · %H:%M}**",
        await _changes(session, local),
        await _values(session, words),
        await _today(session, local),
        await _checks(session, local),
        await _time(session, local),
    )
    return "\n\n".join(block for block in blocks if block)


async def _today(session: AsyncSession, local: datetime) -> str:
    """Every Action in Today, in Today's order, each under its Goals and Subgoals."""
    cards = await order_cards(session, await today_actions(session), now=local)
    if not cards:
        return "**☀️ Today** · Nothing is planned for today."
    load = await plan_load(session, cards, start_date=local.date(), end_date=local.date())
    planned = f"Planned: {'at least ' if load.unknown_schedules else ''}{load.actions} Actions"
    if await effort_tracking_on(session):
        planned += f" · {effort_label(load.effort)} EP"
        if load.unestimated:
            planned += f" · {load.unestimated} Actions have no estimate; EP total is partial"
    if load.unknown_schedules:
        planned += " · Schedule quantities are unknown"
    lines = [f"**☀️ Today · {len(cards)}**", planned]
    for row in await goal_tree(session, cards, now=local):
        card = row.card
        # Only an Action has executions to count; a Goal stands for the Actions under it.
        quantity = load.counts.get(card.id, 1)
        suffix = f" × {quantity if quantity is not None else '?'}" if quantity != 1 else ""
        if row.also_under:
            suffix += " · ↖ " + ", ".join(
                _cite("card", parent.id, parent.title) for parent in row.also_under
            )
        lines.append(f"{_INDENT * row.depth}{_cite('card', card.id, card.title)}{suffix}")
    return "\n".join(lines)


async def _checks(session: AsyncSession, local: datetime) -> str:
    checks = await checks_today(session, now=local)
    if not checks:
        return ""
    lines = [f"**☑️ Checks today · {len(checks)}**"]
    for check, count in checks:
        suffix = f" × {count}" if count > 1 else ""
        lines.append(f"{_cite('check', check.id, check.title)}{suffix}")
    return "\n".join(lines)


async def _values(session: AsyncSession, words: Mapping[int, str]) -> str:
    values = await values_in_focus(session)
    if not values:
        return ""
    lines = ["**💎 Values in focus**"]
    for value, _ in values:
        said = words.get(value.id)
        cited = _cite("value", value.id, value.name)
        lines.append(f"{cited} — {_plain(said)}" if said else cited)
    return "\n".join(lines)


async def _time(session: AsyncSession, local: datetime) -> str:
    if not await time_tracking_on(session):
        return ""
    midnight = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    minutes, count = await tracked_between(session, midnight, midnight + timedelta(days=1))
    if not count:
        return "⌛ Tracked today: nothing yet"
    return f"⌛ Tracked today: {minutes_label(minutes)} over {count} Action{'s' * (count != 1)}"


async def _changes(session: AsyncSession, local: datetime) -> str:
    rows = list(
        await session.scalars(select(LogEvent).order_by(LogEvent.id.desc()).limit(HOME_LOG_SHOWN))
    )
    if not rows:
        return ""
    lines = ["**🗒 Latest changes**"]
    for row in rows:
        at = row.at.astimezone(local.tzinfo)
        when = f"{at:%H:%M}" if at.date() == local.date() else f"{at:%d.%m %H:%M}"
        title = (
            _plain(row.title)
            if await _deleted_since(session, row)
            else _cite(row.item_type, row.item_id, row.title)
        )
        lines.append(f"{when} {_ICONS.get(row.operation, '✏️')} {title} — {_what(row.operation)}")
    return "\n".join(lines)


async def _deleted_since(session: AsyncSession, row: LogEvent) -> bool:
    """Whether the item was deleted at or after this change: its id may name another since."""
    return (
        await session.scalar(
            select(LogEvent.id)
            .where(
                LogEvent.item_type == row.item_type,
                LogEvent.item_id == row.item_id,
                LogEvent.operation == DELETE,
                LogEvent.id >= row.id,
            )
            .limit(1)
        )
    ) is not None


def _what(operation: str) -> str:
    if operation in _DONE_TO:
        return _DONE_TO[operation]
    for prefix, sign in (("edit_", ""), ("link_", "+"), ("unlink_", "−")):
        if operation.startswith(prefix):
            return sign + operation.removeprefix(prefix).replace("_", " ")
    return operation.replace("_", " ")
