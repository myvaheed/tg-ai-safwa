"""How a Sprint reads to the owner once it is over."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Sprint
from ...telegram.sprint import render_sprint_retro


async def retro_citation_label(session: AsyncSession, services: Any, sprint: Sprint) -> str:
    return f"📊 Sprint {sprint.number} retro"


async def open_sprint_retro(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    """The retro is always its own message: it is what a finished Sprint left behind."""
    await render_sprint_retro(message, services, item_id)
