"""The Sprint the owner committed to, and one row per Action it ever had in scope."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin


class Sprint(Base, TimestampMixin):
    __tablename__ = "sprints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, unique=True)
    planned_start_date: Mapped[date] = mapped_column(Date)
    planned_end_date: Mapped[date] = mapped_column(Date)
    actual_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    success_criteria: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    finish_reason: Mapped[str | None] = mapped_column(String(100))


class SprintCommitment(Base, TimestampMixin):
    """What one Action was worth to one Sprint, frozen when it entered its scope."""

    __tablename__ = "sprint_commitments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sprint_id: Mapped[int] = mapped_column(ForeignKey("sprints.id", ondelete="CASCADE"), index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    effort_snapshot: Mapped[int] = mapped_column(Integer)
    scope_kind: Mapped[str] = mapped_column(String(20), default="initial")
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(20))

    __table_args__ = (UniqueConstraint("sprint_id", "card_id"),)
