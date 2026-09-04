"""Writing a Cue, and reading the one that is next.

This module knows nothing about the bot or the Advisor: a producer writes into its own
transaction, and the delivery poll is somewhere else entirely.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .model import Cue


async def cue_advisor(session: AsyncSession, *, text: str) -> Cue:
    """Hand the Advisor something to say, to be said once the owner is not busy."""
    cue = Cue(text=text)
    session.add(cue)
    await session.flush()
    return cue


async def next_cue(session: AsyncSession) -> Cue | None:
    """The oldest Cue still waiting."""
    return await session.scalar(select(Cue).order_by(Cue.id).limit(1))
