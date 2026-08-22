"""What another feature may ask the Profile.

One setting at a time, never the row: the entity, its fields and its writes stay behind
this door, so a feature that only needs to know when memory syncs cannot start depending
on the shape of the Profile.
"""

from __future__ import annotations

from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from .model import UserProfile


async def scheduled_memory_time(session: AsyncSession) -> time | None:
    """The local time the owner set for memory maintenance, or None when it is off."""
    profile = await session.get(UserProfile, 1)
    return profile.memory_update_time if profile is not None else None
