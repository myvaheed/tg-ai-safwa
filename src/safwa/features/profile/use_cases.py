"""How the owner profile is written.

Every write names one declared field, because that is what the Profile screen does and
what a saved profile proposal does once per field it carries. A single field also makes the
value's type the field's own business instead of a bag of optional keyword arguments that
each caller has to be trusted to fill correctly.
"""

from __future__ import annotations

import asyncio
from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.access.credentials import hash_secret_word
from tg_agent_shell.foundation.errors import DomainError

from ...foundation.workspace import bump_workspace
from .model import (
    HOME_AFTER_MINUTES_MAX,
    HOME_AFTER_MINUTES_MIN,
    ProfileField,
    ProfileValue,
    UserProfile,
)


def profile_field(name: str) -> ProfileField:
    try:
        return ProfileField(name)
    except ValueError:
        raise DomainError(f"Unsupported profile field: {name}") from None


def validated_profile_value(field: ProfileField, value: ProfileValue) -> ProfileValue:
    """The value as the field stores it, or the DomainError that refuses it."""
    match field:
        case ProfileField.HOME_AFTER_MINUTES:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not HOME_AFTER_MINUTES_MIN <= value <= HOME_AFTER_MINUTES_MAX
            ):
                raise DomainError(
                    f"The quiet time before chat cleanup must be between "
                    f"{HOME_AFTER_MINUTES_MIN} and {HOME_AFTER_MINUTES_MAX} minutes"
                )
        case ProfileField.TIME_TRACKING:
            if not isinstance(value, bool):
                raise DomainError("Time tracking is on or off")
        case ProfileField.EFFORT_TRACKING:
            if not isinstance(value, bool):
                raise DomainError("Effort Points are on or off")
        case ProfileField.MORNING_TIME | ProfileField.EVENING_TIME:
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


async def set_profile_field(
    session: AsyncSession, field: ProfileField, value: ProfileValue
) -> UserProfile:
    """Store one validated profile value. Whatever reads it reads it live."""
    validated = validated_profile_value(field, value)
    profile = await require_profile(session)
    setattr(profile, field.value, validated)
    await bump_workspace(session)
    return profile


async def set_secret_word(session: AsyncSession, raw: str | None) -> None:
    profile = await require_profile(session)
    profile.secret_word_hash = (
        None if raw is None else await asyncio.to_thread(hash_secret_word, raw)
    )
    await bump_workspace(session)
