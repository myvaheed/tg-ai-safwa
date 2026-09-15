"""What another feature may ask the Profile.

One setting at a time, never the row: the entity, its fields and its writes stay behind
this door, so a feature that only needs to know when memory syncs cannot start depending
on the shape of the Profile.
"""

from __future__ import annotations

from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from .model import SPRINT_LENGTH_DAYS, UserProfile


async def scheduled_memory_time(session: AsyncSession) -> time | None:
    """The local time the owner set for memory maintenance, or None when it is off."""
    profile = await session.get(UserProfile, 1)
    return profile.memory_update_time if profile is not None else None


async def sprint_length_days(session: AsyncSession) -> int:
    """How many days the owner wants a Sprint to run, or the default nobody changed."""
    profile = await session.get(UserProfile, 1)
    return profile.sprint_length_days if profile is not None else SPRINT_LENGTH_DAYS


async def capacity_effort_points(session: AsyncSession) -> float | None:
    """The effort the owner means to take on in a Sprint, or None when it is off."""
    profile = await session.get(UserProfile, 1)
    return profile.capacity_effort_points if profile is not None else None


async def hook_switched_on(session: AsyncSession, name: str) -> bool:
    """Whether the owner left the automatic reaction of that name on: the shell's policy."""
    profile = await session.get(UserProfile, 1)
    return profile is None or name not in profile.disabled_hooks
