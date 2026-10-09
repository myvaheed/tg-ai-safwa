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
    EVENING_TIME_DEFAULT,
    HOME_AFTER_MINUTES_DEFAULT,
    MORNING_TIME_DEFAULT,
    UserProfile,
)

# The question after an Action is Done without its time. It is the Cards' hook, named here
# because the Profile decides when it is silent: while Time tracking is off, whatever its
# own switch says.
TIME_TRACKING_REMINDER = "cards.time_tracking_reminder"
EFFORT_TRACKING_REMINDER = "cards.effort_tracking_reminder"
TODAY_OVERLOAD = "cards.today_overload"


async def effort_tracking_on(session: AsyncSession) -> bool:
    """Whether the owner uses Effort Points to estimate load."""
    profile = await session.get(UserProfile, 1)
    return profile is not None and profile.effort_tracking


async def morning_time(session: AsyncSession) -> time:
    """The local time the morning checks run at: what their daily hooks read."""
    profile = await session.get(UserProfile, 1)
    return profile.morning_time if profile is not None else time.fromisoformat(MORNING_TIME_DEFAULT)


async def evening_time(session: AsyncSession) -> time:
    """The local time the Diary nudge and the daily summary come at."""
    profile = await session.get(UserProfile, 1)
    return profile.evening_time if profile is not None else time.fromisoformat(EVENING_TIME_DEFAULT)


async def diary_instructions(session: AsyncSession) -> str:
    """The owner's standing instruction for the Diary, or nothing."""
    profile = await session.get(UserProfile, 1)
    return profile.diary_instructions if profile is not None else ""


async def time_tracking_on(session: AsyncSession) -> bool:
    """Whether the owner records the time an Action took."""
    profile = await session.get(UserProfile, 1)
    return profile is not None and profile.time_tracking


async def about_me(session: AsyncSession) -> str:
    """What the owner wrote about themselves, or nothing."""
    profile = await session.get(UserProfile, 1)
    return profile.about_me if profile is not None else ""


async def home_after_minutes(session: AsyncSession) -> int:
    """How long the owner may leave the chat before it is cleared down to the Home dashboard."""
    profile = await session.get(UserProfile, 1)
    return profile.home_after_minutes if profile is not None else HOME_AFTER_MINUTES_DEFAULT


async def secret_word_verifier(session: AsyncSession) -> str | None:
    profile = await session.get(UserProfile, 1)
    return profile.secret_word_hash if profile is not None else None


async def active_day_minutes(session: AsyncSession) -> int:
    """How long the owner's active day is: from the Morning time to the Evening time.

    An Evening time before the Morning time is a day that runs past midnight; the two equal
    is a day of no length.
    """
    start, end = await morning_time(session), await evening_time(session)
    return (end.hour * 60 + end.minute - start.hour * 60 - start.minute) % (24 * 60)


async def hook_switched_on(session: AsyncSession, name: str) -> bool:
    """Whether the owner left the automatic reaction of that name on: the shell's policy."""
    profile = await session.get(UserProfile, 1)
    if name == TIME_TRACKING_REMINDER and (profile is None or not profile.time_tracking):
        return False
    if name in {TODAY_OVERLOAD, EFFORT_TRACKING_REMINDER} and (
        profile is None or not profile.effort_tracking
    ):
        return False
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
