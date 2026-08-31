"""Persisted state for dialogue summaries and authoritative memory synchronization."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from ...foundation.models import Base

# Written on the first message of a Summary, for the owner. The window strips it back off,
# so the model reads the words alone — one string, written and stripped from here.
SUMMARY_HEADER = "📜 Summary"


class SummaryState(Base):
    __tablename__ = "summary_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    summary_message_id: Mapped[int | None] = mapped_column(Integer)
    covered_message_id: Mapped[int | None] = mapped_column(Integer)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryFactCache(Base):
    __tablename__ = "memory_fact_cache"
    line_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(20), default="manual")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemorySyncState(Base):
    __tablename__ = "memory_sync_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    file_mtime: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    processed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memory_last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
