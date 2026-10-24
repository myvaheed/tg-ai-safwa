"""The persisted Reminder."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Date, Index, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin, UtcDateTime


class ScheduleKind(StrEnum):
    """How a Reminder comes due. Derived from the resolved parameters, never model-supplied."""

    ONCE = "once"
    INTERVAL = "interval"
    DAILY = "daily"
    WEEKLY = "weekly"
    # On days of the month; 31 is the last day of every month.
    MONTHLY = "monthly"
    # On answers of Checks or Actions, never by the clock.
    CONDITION = "condition"


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

    A Reminder on answers has no `next_fire_at`: `condition` holds the items, the series of
    each, the answer, the count and whether in a row, and `reminders.condition` writes
    its Cue when an answer makes that count.

    A repeat may end: `until` is the last local day it fires on and `total` how many times it
    fires in all, counted in `fired`. After its last fire it deletes itself, as a one-shot does.

    One Remind made may fire `lead_minutes` before each of its moments; `next_fire_at` is then
    that earlier instant, and every other column still names the moments.
    """

    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instruction: Mapped[str] = mapped_column(Text)

    schedule_kind: Mapped[str] = mapped_column(String(20))
    weekdays: Mapped[list[str]] = mapped_column(JSON, default=list)
    month_days: Mapped[list[int]] = mapped_column(JSON, default=list)
    at_time: Mapped[time | None] = mapped_column(Time)
    # UTC.  The one-shot moment, or the moment a recurrence starts; a floor, never a rhythm.
    anchor_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)

    condition: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    until: Mapped[date | None] = mapped_column(Date)
    total: Mapped[int | None] = mapped_column(Integer)
    fired: Mapped[int] = mapped_column(Integer, default=0)
    lead_minutes: Mapped[int] = mapped_column(Integer, default=0)

    next_fire_at: Mapped[datetime | None] = mapped_column(UtcDateTime, index=True)
    item_type: Mapped[str | None] = mapped_column(String(10))
    item_id: Mapped[int | None] = mapped_column(Integer)

    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (Index("ix_reminders_item", "item_type", "item_id", unique=True),)
