"""How the owner profile is written.

Every write names one declared field, because that is what the Profile screen does and
nothing else writes the Profile at all: the AI holds no profile tool.  A single field also
makes the value's type the field's own business instead of a bag of optional keyword
arguments that each caller has to be trusted to fill correctly.
"""

from __future__ import annotations

from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.cues.queue import drop_hook_cue
from tg_agent_shell.foundation.errors import DomainError

from ...constants import SPRINT_LENGTH_MAX_DAYS, SPRINT_LENGTH_MIN_DAYS
from ...foundation.workspace import bump_workspace
from .model import ProfileField, ProfileValue, UserProfile


def profile_field(name: str) -> ProfileField:
    try:
        return ProfileField(name)
    except ValueError:
        raise DomainError(f"Unsupported profile field: {name}") from None


def _validated(field: ProfileField, value: ProfileValue) -> ProfileValue:
    match field:
        case ProfileField.SPRINT_LENGTH_DAYS:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not SPRINT_LENGTH_MIN_DAYS <= value <= SPRINT_LENGTH_MAX_DAYS
            ):
                raise DomainError(
                    f"Sprint length must be between {SPRINT_LENGTH_MIN_DAYS} and "
                    f"{SPRINT_LENGTH_MAX_DAYS} days"
                )
        case ProfileField.CAPACITY_EFFORT_POINTS:
            # Half a rung exists, so a Sprint's capacity is a number and not a count.
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int | float) or value <= 0
            ):
                raise DomainError("Sprint capacity must be a positive number, or off")
        case ProfileField.DIARY_TIME | ProfileField.SUMMARY_TIME | ProfileField.MORNING_TIME:
            # Never off: the hook that reads each has a switch of its own.
            if not isinstance(value, time):
                raise DomainError(f"{field.value} must be a clock time")
        case _:
            if not isinstance(value, str):
                raise DomainError(f"{field.value} must be text")
    return value


async def require_profile(session: AsyncSession) -> UserProfile:
    profile = await session.get(UserProfile, 1)
    if profile is None:
        raise DomainError("User profile is not initialized")
    return profile


async def set_hook_switch(session: AsyncSession, name: str, *, on: bool) -> UserProfile:
    """Turn one automatic reaction off or on; the screen says which names have a switch.

    A flip either way starts the hook from nothing pending: off takes its request with it,
    and on drops whatever a change handed on late wrote while the hook was off.
    """
    profile = await require_profile(session)
    was_on = name not in profile.disabled_hooks
    disabled = [hook for hook in profile.disabled_hooks if hook != name]
    profile.disabled_hooks = disabled if on else [*disabled, name]
    if on != was_on:
        await drop_hook_cue(session, name)
    return profile


async def set_profile_field(
    session: AsyncSession, field: ProfileField, value: ProfileValue
) -> UserProfile:
    """Store one validated profile value. Whatever reads it reads it live."""
    validated = _validated(field, value)
    profile = await require_profile(session)
    setattr(profile, field.value, validated)
    await bump_workspace(session)
    return profile
