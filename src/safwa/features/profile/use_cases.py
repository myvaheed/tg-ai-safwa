"""How the owner profile is written.

Every write names one declared field, because that is what the Profile screen does and
nothing else writes the Profile at all: the AI holds no profile tool.  A single field also
makes the value's type the field's own business instead of a bag of optional keyword
arguments that each caller has to be trusted to fill correctly.
"""

from __future__ import annotations

from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import Clock
from tg_agent_shell.foundation.errors import DomainError

from ...constants import SPRINT_LENGTH_MAX_DAYS, SPRINT_LENGTH_MIN_DAYS
from ...foundation.workspace import bump_workspace
from ..reminders.model import Reminder
from ..reminders.use_cases import sync_daily_system_reminder
from .model import ProfileField, ProfileValue, UserProfile

DIARY_REMINDER_INSTRUCTION = (
    "End of day. Call the diary subagent for today, then propose what it reports."
)


# Changing either of these is what the Diary's own trigger is derived from.
_DIARY_TRIGGER_FIELDS = frozenset({ProfileField.DIARY_TIME, ProfileField.DIARY_INSTRUCTIONS})


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
        case ProfileField.MEMORY_UPDATE_TIME | ProfileField.DIARY_TIME:
            if value is not None and not isinstance(value, time):
                raise DomainError(f"{field.value} must be a clock time, or off")
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
    session: AsyncSession, field: ProfileField, value: ProfileValue, *, clock: Clock
) -> UserProfile:
    """Store one validated profile value, with whatever it is the source of truth for."""
    validated = _validated(field, value)
    profile = await require_profile(session)
    setattr(profile, field.value, validated)
    if field in _DIARY_TRIGGER_FIELDS:
        await sync_diary_reminder(session, clock=clock, profile=profile)
    await bump_workspace(session)
    return profile


async def sync_diary_reminder(
    session: AsyncSession, *, clock: Clock, profile: UserProfile | None = None
) -> Reminder | None:
    """Say what the Diary trigger should say and when; Reminders keeps the row."""
    current = profile or await require_profile(session)
    extra = current.diary_instructions.strip()
    return await sync_daily_system_reminder(
        session,
        instruction=f"{DIARY_REMINDER_INSTRUCTION} {extra}" if extra else DIARY_REMINDER_INSTRUCTION,
        at_time=current.diary_time,
        clock=clock,
    )
