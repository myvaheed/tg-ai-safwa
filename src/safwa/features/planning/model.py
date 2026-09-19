"""The Sprint the owner committed to, and one row per Action it ever had in scope."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin, UtcDateTime


class SprintStatus(StrEnum):
    ACTIVE = "active"
    FINISHED = "finished"


# A Sprint number is `yy.MM-xx`: the month it started in, then its place in that month.
# Zero-padded so text order is start order, and eight characters wide for the century
# this product lives in.
SPRINT_NUMBER_WIDTH = 8


def next_sprint_number(start: date, used_this_month: Iterable[str]) -> str:
    """The label a Sprint starting on this day takes.

    The sequence follows the highest ever used in that month, so deleting a Sprint
    cannot hand its label to another — which is what the integer already guaranteed. A
    Sprint that crosses a month boundary keeps the month it began in.
    """
    prefix = f"{start:%y.%m}"
    taken = [
        int(number.rpartition("-")[2])
        for number in used_this_month
        if number.startswith(f"{prefix}-")
    ]
    return f"{prefix}-{max(taken, default=0) + 1:02d}"


class Sprint(Base, TimestampMixin):
    __tablename__ = "sprints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(SPRINT_NUMBER_WIDTH), unique=True)
    planned_start_date: Mapped[date] = mapped_column(Date)
    planned_end_date: Mapped[date] = mapped_column(Date)
    actual_started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    actual_ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    success_criteria: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=SprintStatus.ACTIVE.value)
    finish_reason: Mapped[str | None] = mapped_column(String(100))
    # What the Sprint added up to as it ended (`closing.RetroStatistics.as_record`); None
    # while it runs.
    retro: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Whether the Success criteria were met, in the owner's word on the retro screen; None
    # until they say.
    criterion_met: Mapped[bool | None] = mapped_column(Boolean)
    # What the retro analysis made of the record, as its last run left it; None until one
    # ran to the end.
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # When memory took the analysis in; None while an analysis is still owed to it, so a
    # run that wrote a new one sets it back and the memory poll finds the Sprint again.
    memory_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class SprintCommitment(Base, TimestampMixin):
    """What one Action was worth to one Sprint, frozen when it entered its scope."""

    __tablename__ = "sprint_commitments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sprint_id: Mapped[int] = mapped_column(ForeignKey("sprints.id", ondelete="CASCADE"), index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    effort_snapshot: Mapped[float] = mapped_column(Float)
    scope_kind: Mapped[str] = mapped_column(String(20), default="initial")
    added_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    removed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    result: Mapped[str | None] = mapped_column(String(20))
    # Whether the Sprint's Success criterion rests on this Action, as the model marked it;
    # None until the model has answered about this row.
    key_action: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (UniqueConstraint("sprint_id", "card_id"),)
