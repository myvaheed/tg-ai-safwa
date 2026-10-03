"""What another feature reads of Life in weeks: the grid of the owner's life."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .model import LifeSettings
from .weeks import LifeGrid


async def life_grid(session: AsyncSession) -> LifeGrid | None:
    """The grid the Settings draw, or None while no birth date is set."""
    settings = await session.get(LifeSettings, 1)
    if settings is None or settings.birth_date is None:
        return None
    return LifeGrid(settings.birth_date, settings.years)
