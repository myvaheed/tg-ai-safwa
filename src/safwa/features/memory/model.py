"""Persisted state for authoritative memory synchronization."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from tg_agent_shell.foundation.models import Base, UtcDateTime


class MemoryFactCache(Base):
    __tablename__ = "memory_fact_cache"
    line_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(20), default="manual")
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )


class MemorySyncState(Base):
    __tablename__ = "memory_sync_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    file_mtime: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    processed_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    memory_last_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )
