"""Intervals of owner activity, independent of the kept conversation."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..foundation.models import Base, UtcDateTime

IDLE_WINDOW = timedelta(minutes=2)


class UsageInterval(Base):
    __tablename__ = "usage_intervals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, index=True)
    event_key: Mapped[str] = mapped_column(String(160))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime] = mapped_column(UtcDateTime)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("owner_id", "event_key"),)
