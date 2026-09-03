"""The retro screen: the same Sprint, read after it closed.

It is what a `retro:` citation opens, and it is empty — the retrospective itself is not
built yet, and this is where it goes when it is.
"""

from __future__ import annotations

import html
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...adapters.kinds import MessageKind
from ...foundation.errors import DomainError
from ...shell import Services, menu_row, send_registered
from ..planning.model import Sprint


async def render_retro(message: Message, services: Services, sprint_id: int) -> None:
    async with services.sessions() as session:
        sprint = await session.get(Sprint, sprint_id)
        if sprint is None:
            raise DomainError("Sprint does not exist")
        text = (
            f"<b>Sprint {sprint.number} retro</b>\n"
            f"{sprint.planned_start_date} – {sprint.planned_end_date}\n"
            f"Success criteria: {html.escape(sprint.success_criteria)}\n\n"
            "There is nothing here yet."
        )
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
