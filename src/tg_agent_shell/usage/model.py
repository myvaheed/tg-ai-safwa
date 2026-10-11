"""The owner's accumulated usage, independent of the kept conversation."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Float, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..foundation.models import Base, UtcDateTime

IDLE_WINDOW = timedelta(minutes=2)


class OwnerUsage(Base):
    __tablename__ = "owner_usage"
    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    last_at: Mapped[datetime] = mapped_column(UtcDateTime)
