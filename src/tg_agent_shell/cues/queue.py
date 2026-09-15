"""Writing a Cue, and reading the one that is next.

This module knows nothing about the bot or the Advisor: a producer writes into its own
transaction, and the delivery poll is somewhere else entirely.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .model import Cue


async def add_cue(session: AsyncSession, *, text: str) -> Cue:
    """Queue something to be said, once the owner is not busy."""
    cue = Cue(text=text)
    session.add(cue)
    await session.flush()
    return cue


async def merge_hook_cue(session: AsyncSession, *, hook: str, items: Sequence[Any]) -> Cue:
    """Add these items to the hook's one pending request, starting it if there is none."""
    cue = await session.scalar(select(Cue).where(Cue.hook == hook))
    if cue is None:
        cue = Cue(hook=hook, payload=list(items))
        session.add(cue)
        await session.flush()
        return cue
    known = cue.payload or []
    cue.payload = [*known, *(item for item in items if item not in known)]
    return cue


async def drop_hook_cue(session: AsyncSession, hook: str) -> None:
    """Forget the hook's pending request; nothing brings it back."""
    await session.execute(delete(Cue).where(Cue.hook == hook))


async def next_cue(session: AsyncSession) -> Cue | None:
    """The oldest Cue still waiting."""
    return await session.scalar(select(Cue).order_by(Cue.id).limit(1))
