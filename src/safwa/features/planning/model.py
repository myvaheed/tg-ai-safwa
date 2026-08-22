"""The two classifications a Card carries, and the link rows that hold them.

A Value is a focus the owner names; a Tag is a label for finding Cards. Both are linked
to Cards the same way, and a Value is additionally linked to Checks — a Check is what
shows how well a Value is actually being held to.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin


class Value(Base, TimestampMixin):
    __tablename__ = "values"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class CardValue(Base):
    __tablename__ = "card_values"
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    value_id: Mapped[int] = mapped_column(
        ForeignKey("values.id", ondelete="CASCADE"), primary_key=True
    )


class CardTag(Base):
    __tablename__ = "card_tags"
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class CheckValue(Base):
    """A Value carried by a Check, which says nothing about that Check's Cards."""

    __tablename__ = "check_values"
    check_id: Mapped[int] = mapped_column(
        ForeignKey("checks.id", ondelete="CASCADE"), primary_key=True
    )
    value_id: Mapped[int] = mapped_column(
        ForeignKey("values.id", ondelete="CASCADE"), primary_key=True, index=True
    )
