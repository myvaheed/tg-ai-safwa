"""What another feature may ask the Profile.

One setting at a time, never the row: the entity, its fields and its writes stay behind
this door, so a feature that only needs one answer cannot start depending on the shape of
the Profile. The one write here is a hook's switch, which the Profile screen turns and the
onboarding proposal turns off.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.cues.queue import drop_hook_cue
from tg_agent_shell.foundation.errors import DomainError

from .model import (
    DIARY_TIME_DEFAULT,
    MORNING_TIME_DEFAULT,
    SPRINT_LENGTH_DAYS,
    SUMMARY_TIME_DEFAULT,
    UserProfile,
)


async def sprint_length_days(session: AsyncSession) -> int:
    """How many days the owner wants a Sprint to run, or the default nobody changed."""
    profile = await session.get(UserProfile, 1)
    return profile.sprint_length_days if profile is not None else SPRINT_LENGTH_DAYS


async def capacity_effort_points(session: AsyncSession) -> float | None:
    """The effort the owner means to take on in a Sprint, or None when it is off."""
    profile = await session.get(UserProfile, 1)
    return profile.capacity_effort_points if profile is not None else None


async def morning_time(session: AsyncSession) -> time:
    """The local time the morning checks run at: what their daily hooks read."""
    profile = await session.get(UserProfile, 1)
    return profile.morning_time if profile is not None else time.fromisoformat(MORNING_TIME_DEFAULT)


async def diary_time(session: AsyncSession) -> time:
    """The local time the Diary nudge comes at."""
    profile = await session.get(UserProfile, 1)
    return profile.diary_time if profile is not None else time.fromisoformat(DIARY_TIME_DEFAULT)


async def diary_instructions(session: AsyncSession) -> str:
    """The owner's standing instruction for the Diary, or nothing."""
    profile = await session.get(UserProfile, 1)
    return profile.diary_instructions if profile is not None else ""


async def summary_time(session: AsyncSession) -> time:
    """The local time the daily summary comes at."""
    profile = await session.get(UserProfile, 1)
    return profile.summary_time if profile is not None else time.fromisoformat(SUMMARY_TIME_DEFAULT)


async def hook_switched_on(session: AsyncSession, name: str) -> bool:
    """Whether the owner left the automatic reaction of that name on: the shell's policy."""
    profile = await session.get(UserProfile, 1)
    return profile is None or name not in profile.disabled_hooks


async def set_hook_switch(
    session: AsyncSession, name: str, *, on: bool, followers: Iterable[str] = ()
) -> UserProfile:
    """Turn one automatic reaction off or on, with the hooks that follow its switch.

    The screen says which names have a switch, and the registry which hooks follow one. A
    flip either way starts each of them from nothing pending: off takes their requests with
    it, and on drops whatever a change handed on late wrote while they were off. A turn that
    changes nothing drops nothing.
    """
    profile = await session.get(UserProfile, 1)
    if profile is None:
        raise DomainError("User profile is not initialized")
    was_on = name not in profile.disabled_hooks
    disabled = [hook for hook in profile.disabled_hooks if hook != name]
    profile.disabled_hooks = disabled if on else [*disabled, name]
    if on != was_on:
        for hook in (name, *followers):
            await drop_hook_cue(session, hook)
    return profile
