"""The persisted Reminder."""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from tg_agent_shell.foundation.models import Base, TimestampMixin, UtcDateTime


class ScheduleKind(StrEnum):
    """How a Reminder repeats. Derived from the resolved parameters, never model-supplied."""

    ONCE = "once"
    INTERVAL = "interval"
    DAILY = "daily"
    WEEKLY = "weekly"


class Reminder(Base, TimestampMixin):
    """A trigger the owner set: instruction text plus a schedule, and nothing else.

    Deletion is the only off switch; there is no `archived_at` and no `active` flag. The
    subject is named inside `instruction` as `#id` text rather than by a foreign key, so
    one Reminder may concern any number of Safwa items of any type.

    `next_fire_at` is the only column the scheduler poll reads, and it says **when** and
    nothing more: the poll writes the words down as a Cue and moves this row on in the same
    transaction, and the Cue row is what survives until the owner has them.
    """

    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instruction: Mapped[str] = mapped_column(Text)
    # Set only on the Reminders a Sprint start creates, so finishing that Sprint can
    # remove them; an owner-created Reminder never carries one.
    sprint_id: Mapped[int | None] = mapped_column(
        ForeignKey("sprints.id", ondelete="CASCADE"), index=True
    )
    # A Reminder no owner set — the Settings trigger, a Sprint's end warnings: hidden from
    # `/reminders` and from `ai_reminders`, and refused by the edit and delete paths.
    system: Mapped[bool] = mapped_column(Boolean, default=False)

    schedule_kind: Mapped[str] = mapped_column(String(20))
    weekdays: Mapped[list[str]] = mapped_column(JSON, default=list)
    at_time: Mapped[time | None] = mapped_column(Time)
    # UTC.  The one-shot moment, or the moment a recurrence starts; a floor, never a rhythm.
    anchor_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    quiet_windows: Mapped[list[str]] = mapped_column(JSON, default=list)

    next_fire_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)

    version: Mapped[int] = mapped_column(Integer, default=1)
