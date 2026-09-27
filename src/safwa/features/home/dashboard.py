"""The Home dashboard: what a quiet chat is cleared down to, built from the workspace.

Four blocks, each read through the door of the feature that owns it: the next Actions, the
Values in focus with the words written for them, the time tracked today, and the last
changes. Every item is a citation, so it reads and links the way it does in an answer.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.telegram import Services, render_citations

from ...foundation.log_events import CREATE, DELETE, UPDATE, LogEvent
from ...foundation.workspace import require_workspace
from ..cards.api import (
    Card,
    CardStage,
    actions_on_stages,
    goal_of,
    list_order,
    minutes_label,
    tracked_between,
)
from ..planning.api import today_actions
from ..profile.api import time_tracking_on
from ..values.api import values_in_focus

# How many Actions the dashboard opens with, and how many changes it ends with.
HOME_ACTIONS_SHOWN = 5
HOME_LOG_SHOWN = 10

_INDENT = "    "

# Where the Actions come from: the first of these lists that holds any.
_LISTS = (
    (CardStage.TODAY, "☀️ Today"),
    (CardStage.SPRINT, "🏃 Sprint"),
    (CardStage.BACKLOG, "📚 Backlog"),
)

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
    "set_parent": "parent",
}


def _cite(item_type: str, item_id: int, words: str) -> str:
    """A citation `render_citations` turns into a link, or into its words when it is gone."""
    plain = words.replace("[", "(").replace("]", ")").replace("\n", " ")[:60]
    label = html.escape(plain, quote=False)
    return f"[{label}]({item_type}:{item_id})"


async def dashboard_text(
    session: AsyncSession,
    services: Services,
    words: Mapping[int, str],
    *,
    now: datetime | None = None,
) -> str:
    """The dashboard as Telegram HTML, `words` under the Values they were written for."""
    tz = ZoneInfo((await require_workspace(session)).timezone)
    local = (now or utcnow()).astimezone(tz)
    blocks = (
        f"<b>🏠 {local:%a, %d %b}</b>",
        await _actions(session),
        await _values(session, words),
        await _time(session, local),
        await _changes(session, services, local),
    )
    return await render_citations(session, services, "\n\n".join(block for block in blocks if block))


async def _first_list(session: AsyncSession) -> tuple[str, list[Card]] | None:
    """Today in its own order, else the Sprint, else the Backlog, as every Card list orders."""
    for stage, heading in _LISTS:
        cards = (
            await today_actions(session)
            if stage is CardStage.TODAY
            else sorted(await actions_on_stages(session, stage), key=list_order)
        )
        if cards:
            return heading, cards
    return None


async def _actions(session: AsyncSession) -> str:
    """The first Actions of the first list that holds any, each under its Goal."""
    found = await _first_list(session)
    if found is None:
        return "Nothing is planned yet."
    heading, cards = found
    goals: dict[int, Card] = {}
    under: dict[int | None, list[Card]] = {}
    for action in cards[:HOME_ACTIONS_SHOWN]:
        goal = await goal_of(session, action)
        if goal is not None:
            goals[goal.id] = goal
        under.setdefault(goal.id if goal else None, []).append(action)
    lines = [f"<b>{heading} · {min(len(cards), HOME_ACTIONS_SHOWN)} of {len(cards)}</b>"]
    for goal_id, actions in sorted(under.items(), key=lambda item: item[0] is None):
        indent = ""
        if goal_id is not None:
            lines.append(_cite("card", goal_id, goals[goal_id].title))
            indent = _INDENT
        lines += [f"{indent}{_cite('card', action.id, action.title)}" for action in actions]
    return "\n".join(lines)


async def _values(session: AsyncSession, words: Mapping[int, str]) -> str:
    values = await values_in_focus(session)
    if not values:
        return ""
    lines = ["<b>💎 Values in focus</b>"]
    for value in values:
        said = words.get(value.id)
        cited = _cite("value", value.id, value.name)
        lines.append(f"{cited} — {html.escape(said)}" if said else cited)
    return "\n".join(lines)


async def _time(session: AsyncSession, local: datetime) -> str:
    if not await time_tracking_on(session):
        return ""
    midnight = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    minutes, count = await tracked_between(session, midnight, midnight + timedelta(days=1))
    if not count:
        return "⌛ Tracked today: nothing yet"
    return f"⌛ Tracked today: {minutes_label(minutes)} over {count} Action{'s' * (count != 1)}"


async def _changes(session: AsyncSession, services: Services, local: datetime) -> str:
    rows = list(
        await session.scalars(select(LogEvent).order_by(LogEvent.id.desc()).limit(HOME_LOG_SHOWN))
    )
    if not rows:
        return ""
    lines = ["<b>🗒 Latest changes</b>"]
    for row in rows:
        at = row.at.astimezone(local.tzinfo)
        when = f"{at:%H:%M}" if at.date() == local.date() else f"{at:%d.%m %H:%M}"
        title = (
            _cite(row.item_type, row.item_id, row.title)
            if row.item_type in services.screens.by_type and not await _deleted_since(session, row)
            else html.escape(row.title)
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
