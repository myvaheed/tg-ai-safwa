"""Public profile surface used by feature consumers and application adapters."""

from datetime import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...constants import SPRINT_LENGTH_MAX_DAYS, SPRINT_LENGTH_MIN_DAYS
from ...foundation.errors import DomainError
from ...foundation.workspace import bump_workspace
from .model import UserProfile
from .use_cases import DIARY_REMINDER_INSTRUCTION, sync_diary_reminder


async def update_profile(session: AsyncSession, **fields: Any) -> UserProfile:
    """Validate and store one explicit profile/settings mutation."""
    allowed = {
        "about_me",
        "advisor_instructions",
        "capacity_effort_points",
        "sprint_length_days",
        "memory_update_time",
        "diary_time",
        "diary_instructions",
    }
    unknown = set(fields).difference(allowed)
    if unknown:
        raise DomainError("Unsupported profile field: " + ", ".join(sorted(unknown)))
    length = fields.get("sprint_length_days")
    if "sprint_length_days" in fields and (
        isinstance(length, bool)
        or not isinstance(length, int)
        or not SPRINT_LENGTH_MIN_DAYS <= length <= SPRINT_LENGTH_MAX_DAYS
    ):
        raise DomainError(
            f"Sprint length must be between {SPRINT_LENGTH_MIN_DAYS} and "
            f"{SPRINT_LENGTH_MAX_DAYS} days"
        )
    capacity = fields.get("capacity_effort_points")
    if "capacity_effort_points" in fields and capacity is not None and (
        isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1
    ):
        raise DomainError("Sprint capacity must be a positive whole number, or off")
    for field_name in ("memory_update_time", "diary_time"):
        clock = fields.get(field_name)
        if field_name in fields and clock is not None and not isinstance(clock, time):
            raise DomainError(f"{field_name} must be a clock time, or off")
    profile = await session.get(UserProfile, 1)
    if profile is None:
        raise DomainError("User profile is not initialized")
    for field_name, value in fields.items():
        setattr(profile, field_name, value)
    if {"diary_time", "diary_instructions"} & set(fields):
        await sync_diary_reminder(session, profile=profile)
    await bump_workspace(session)
    return profile

__all__ = [
    "DIARY_REMINDER_INSTRUCTION",
    "UserProfile",
    "sync_diary_reminder",
    "update_profile",
]
