"""The singleton owner profile and feature settings."""

from __future__ import annotations

from datetime import time
from enum import StrEnum

from sqlalchemy import JSON, Boolean, Integer, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin

# The local clocks the daily hooks read, out of the box: the morning checks, and in the
# evening the Diary nudge and the daily summary. Never off: each hook has a switch of its own.
MORNING_TIME_DEFAULT = "09:00"
EVENING_TIME_DEFAULT = "21:00"
# How many minutes the owner may leave the chat before it is cleared down to the Home
# dashboard, out of the box, and what the Profile accepts.
HOME_AFTER_MINUTES_DEFAULT = 30
HOME_AFTER_MINUTES_MIN = 5
HOME_AFTER_MINUTES_MAX = 1440


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    about_me: Mapped[str] = mapped_column(Text, default="")
    advisor_instructions: Mapped[str] = mapped_column(Text, default="")
    diary_instructions: Mapped[str] = mapped_column(Text, default="")
    morning_time: Mapped[time] = mapped_column(
        Time, default=time.fromisoformat(MORNING_TIME_DEFAULT)
    )
    evening_time: Mapped[time] = mapped_column(
        Time, default=time.fromisoformat(EVENING_TIME_DEFAULT)
    )
    # Whether the owner records the time an Action took: the button on an Action, a
    # Sprint's time in its retro, and the question after Done follow it.
    time_tracking: Mapped[bool] = mapped_column(Boolean, default=False)
    effort_tracking: Mapped[bool] = mapped_column(Boolean, default=False)
    home_after_minutes: Mapped[int] = mapped_column(Integer, default=HOME_AFTER_MINUTES_DEFAULT)
    # The automatic reactions the owner turned off, by hook name. A hook that is not
    # here is on, so a new hook needs no column of its own.
    disabled_hooks: Mapped[list[str]] = mapped_column(JSON, default=list)


ProfileValue = str | bool | int | float | time | None


class ProfileField(StrEnum):
    """The profile values the owner may set. The enum is the allowlist."""

    ABOUT_ME = "about_me"
    ADVISOR_INSTRUCTIONS = "advisor_instructions"
    MORNING_TIME = "morning_time"
    EVENING_TIME = "evening_time"
    DIARY_INSTRUCTIONS = "diary_instructions"
    TIME_TRACKING = "time_tracking"
    EFFORT_TRACKING = "effort_tracking"
    HOME_AFTER_MINUTES = "home_after_minutes"
