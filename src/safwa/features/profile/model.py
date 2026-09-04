"""The singleton owner profile and feature settings."""

from __future__ import annotations

from datetime import time
from enum import StrEnum

from sqlalchemy import Integer, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from tg_agent_shell.foundation.models import Base, TimestampMixin

from ...constants import DIARY_TIME_DEFAULT, SPRINT_LENGTH_DAYS


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    about_me: Mapped[str] = mapped_column(Text, default="")
    advisor_instructions: Mapped[str] = mapped_column(Text, default="")
    capacity_effort_points: Mapped[int | None] = mapped_column(Integer)
    sprint_length_days: Mapped[int] = mapped_column(Integer, default=SPRINT_LENGTH_DAYS)
    memory_update_time: Mapped[time | None] = mapped_column(Time)
    diary_time: Mapped[time | None] = mapped_column(
        Time, default=time.fromisoformat(DIARY_TIME_DEFAULT)
    )
    diary_instructions: Mapped[str] = mapped_column(Text, default="")


ProfileValue = str | int | time | None


class ProfileField(StrEnum):
    """The profile values the owner may set. The enum is the allowlist."""

    ABOUT_ME = "about_me"
    ADVISOR_INSTRUCTIONS = "advisor_instructions"
    CAPACITY_EFFORT_POINTS = "capacity_effort_points"
    SPRINT_LENGTH_DAYS = "sprint_length_days"
    MEMORY_UPDATE_TIME = "memory_update_time"
    DIARY_TIME = "diary_time"
    DIARY_INSTRUCTIONS = "diary_instructions"
