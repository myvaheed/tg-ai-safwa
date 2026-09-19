"""What Safwa remembers across Sprints: a pattern, and what each Sprint's analysis observed of it."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from ...foundation.models import Base, UtcDateTime


class MemoryPattern(Base):
    """One thing the retro keeps observing. Its wording, its effect on the day and its
    witnesses are its observations; the row is the identity they share, so a Sprint analysed
    again finds the pattern its claims belonged to."""

    __tablename__ = "memory_pattern"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())


class MemoryObservation(Base):
    """What one Sprint's analysis said of one pattern: the claim as it worded it, and
    whether it raised the day's rating. A Sprint taken in again replaces its own rows alone."""

    __tablename__ = "memory_observation"
    __table_args__ = (UniqueConstraint("sprint_id", "text", "raises"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sprint_id: Mapped[int] = mapped_column(
        ForeignKey("sprints.id", ondelete="CASCADE"), index=True
    )
    pattern_id: Mapped[int] = mapped_column(
        ForeignKey("memory_pattern.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    # True when it raised the day's rating, False when it lowered it.
    raises: Mapped[bool] = mapped_column(Boolean)
