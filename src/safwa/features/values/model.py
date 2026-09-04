"""The Value the owner names, and the two link tables that carry it.

A Card carries a Value when it is work that serves it; a Check carries one when it shows
how well that Value is actually being held to. The two are separate statements.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tg_agent_shell.foundation.models import Base, TimestampMixin


class Value(Base, TimestampMixin):
    __tablename__ = "values"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CardValue(Base):
    __tablename__ = "card_values"
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    value_id: Mapped[int] = mapped_column(
        ForeignKey("values.id", ondelete="CASCADE"), primary_key=True
    )


class CheckValue(Base):
    """A Value carried by a Check, which says nothing about that Check's Cards."""

    __tablename__ = "check_values"
    check_id: Mapped[int] = mapped_column(
        ForeignKey("checks.id", ondelete="CASCADE"), primary_key=True
    )
    value_id: Mapped[int] = mapped_column(
        ForeignKey("values.id", ondelete="CASCADE"), primary_key=True, index=True
    )
