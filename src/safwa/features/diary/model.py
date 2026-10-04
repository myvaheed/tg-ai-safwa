"""The persisted Diary day, and the photos on it."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin, UtcDateTime


class DiaryEntry(Base, TimestampMixin):
    """One local day in the owner's own words, and the photos put on it.

    ``entry_date`` is unique, so a later draft replaces the day rather than joining it.
    Safwa's remark is screen-only and not stored, so the entry keeps one voice.
    ``feeling_score`` is 0-10 and stays NULL for a day that did not say how it felt.
    ``body`` is NULL for a day that holds photos and no words yet.
    """

    __tablename__ = "diary_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_date: Mapped[date] = mapped_column(Date, unique=True)
    body: Mapped[str | None] = mapped_column(Text)
    feeling_score: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)


class DiaryMedia(Base):
    """One photo on a Diary day; it is named by its label, the same everywhere."""

    __tablename__ = "diary_media"
    __table_args__ = (UniqueConstraint("entry_id", "media_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("diary_entries.id"))
    # The photo the shell keeps in `chat_media`, which is another schema's table.
    media_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())
