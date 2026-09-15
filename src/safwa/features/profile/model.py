"""The singleton owner profile and feature settings."""

from __future__ import annotations

from datetime import time
from enum import StrEnum

from sqlalchemy import JSON, Float, Integer, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin

# The default Sprint length; the owner overrides it per workspace in the Profile.
SPRINT_LENGTH_DAYS = 14
# Local clocks the Diary's and the daily summary's system Reminders fire on out of the
# box; the Profile moves each, and `off` there removes that row.
DIARY_TIME_DEFAULT = "22:00"
SUMMARY_TIME_DEFAULT = "20:00"


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    about_me: Mapped[str] = mapped_column(Text, default="")
    advisor_instructions: Mapped[str] = mapped_column(Text, default="")
    capacity_effort_points: Mapped[float | None] = mapped_column(Float)
    sprint_length_days: Mapped[int] = mapped_column(Integer, default=SPRINT_LENGTH_DAYS)
    memory_update_time: Mapped[time | None] = mapped_column(Time)
    diary_time: Mapped[time | None] = mapped_column(
        Time, default=time.fromisoformat(DIARY_TIME_DEFAULT)
    )
    diary_instructions: Mapped[str] = mapped_column(Text, default="")
    summary_time: Mapped[time | None] = mapped_column(
        Time, default=time.fromisoformat(SUMMARY_TIME_DEFAULT)
    )
    # The automatic reactions the owner turned off, by hook name. A hook that is not
    # here is on, so a new hook needs no column of its own.
    disabled_hooks: Mapped[list[str]] = mapped_column(JSON, default=list)


ProfileValue = str | int | float | time | None


class ProfileField(StrEnum):
    """The profile values the owner may set. The enum is the allowlist."""

    ABOUT_ME = "about_me"
    ADVISOR_INSTRUCTIONS = "advisor_instructions"
    CAPACITY_EFFORT_POINTS = "capacity_effort_points"
    SPRINT_LENGTH_DAYS = "sprint_length_days"
    MEMORY_UPDATE_TIME = "memory_update_time"
    DIARY_TIME = "diary_time"
    DIARY_INSTRUCTIONS = "diary_instructions"
    SUMMARY_TIME = "summary_time"
