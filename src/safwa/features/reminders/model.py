"""The persisted Reminder."""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum

from sqlalchemy import JSON, Index, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin, UtcDateTime


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

    A Reminder that Remind made from a Card's or Check's Schedule also names that item in
    `item_type` and `item_id`, so the item can keep it in step; firing never reads them.
    The link lives here because SQLite hands a deleted row's id out again: a Reminder
    deleted by any path takes its link with it.

    `next_fire_at` is the only column the `reminders.fire` tick reads, and it says **when** and
    nothing more: the tick writes the words down as a Cue and moves this row on in the same
    transaction, and the Cue row is what survives until the owner has them.
    """

    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instruction: Mapped[str] = mapped_column(Text)

    schedule_kind: Mapped[str] = mapped_column(String(20))
    weekdays: Mapped[list[str]] = mapped_column(JSON, default=list)
    at_time: Mapped[time | None] = mapped_column(Time)
    # UTC.  The one-shot moment, or the moment a recurrence starts; a floor, never a rhythm.
    anchor_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    quiet_windows: Mapped[list[str]] = mapped_column(JSON, default=list)

    next_fire_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    item_type: Mapped[str | None] = mapped_column(String(10))
    item_id: Mapped[int | None] = mapped_column(Integer)

    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (Index("ix_reminders_item", "item_type", "item_id", unique=True),)
