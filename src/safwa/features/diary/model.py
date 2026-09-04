"""The persisted Diary day."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from tg_agent_shell.foundation.models import Base, TimestampMixin


class DiaryEntry(Base, TimestampMixin):
    """One local day in the owner's own words.

    ``entry_date`` is unique, so a later draft replaces the day rather than joining it.
    Safwa's remark is screen-only and not stored, so the entry keeps one voice.
    ``feeling_score`` is 0-10 and stays NULL for a day that did not say how it felt.
    """

    __tablename__ = "diary_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_date: Mapped[date] = mapped_column(Date, unique=True)
    body: Mapped[str] = mapped_column(Text)
    feeling_score: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
