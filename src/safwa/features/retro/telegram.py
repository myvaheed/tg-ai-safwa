"""The retro screen: the same Sprint, read after it closed.

It is what a `retro:` citation opens: what the Sprint added up to, off its own record.
What Safwa makes of those numbers is not built yet, and this is where it goes when it is.
"""

from __future__ import annotations

import html
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import Services, menu_row, send_registered

from ..cards.api import effort_label
from ..planning.model import Sprint
from .statistics import RetroStatistics, retro_statistics


def retro_text(sprint: Sprint, statistics: RetroStatistics) -> str:
    lines = [
        f"<b>Sprint {sprint.number} retro</b>",
        f"{sprint.planned_start_date} – {sprint.planned_end_date}",
        f"Success criteria: {html.escape(sprint.success_criteria)}",
        "",
        "<b>Effort</b>",
        f"Taken {effort_label(statistics.taken)} EP, finished "
        f"{effort_label(statistics.done)} EP ({statistics.done_share}%)",
        f"Initial plan {effort_label(statistics.initial)} EP, added "
        f"{effort_label(statistics.added)} EP, taken out {effort_label(statistics.removed)} EP",
        "",
        "<b>Actions</b>",
        f"Finished {statistics.finished}, remaining {statistics.remaining}, "
        f"of them blocked {statistics.blocked}",
        "",
        "<b>Checks on a Value</b>",
    ]
    if statistics.series:
        lines.extend(
            f"{html.escape(tally.title)} ({html.escape(', '.join(tally.values))}): "
            f"Passed {tally.passed}, Missed {tally.missed}"
            for tally in statistics.series
        )
    else:
        lines.append("None was answered while the Sprint ran.")
    return "\n".join(lines)


async def render_retro(message: Message, services: Services, sprint_id: int) -> None:
    async with services.sessions() as session:
        sprint = await session.get(Sprint, sprint_id)
        if sprint is None:
            raise DomainError("Sprint does not exist")
        text = retro_text(sprint, await retro_statistics(session, sprint))
        await session.commit()
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def retro_citation_label(session: AsyncSession, services: Any, sprint: Sprint) -> str:
    return f"📊 Sprint {sprint.number} retro"


async def open_retro(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    """The retro is always its own message: it is what a finished Sprint left behind."""
    await render_retro(message, services, item_id)
