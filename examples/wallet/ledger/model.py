"""One movement of money, always positive; which way it went is the category's answer."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..models import Base, TimestampMixin


class Entry(Base, TimestampMixin):
    """One line of the ledger.

    ``version`` is what a review was prepared against: an entry edited between the
    proposal and the Save is refused rather than overwritten.
    """

    __tablename__ = "entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    # Minor units, so nothing here is ever a float.
    amount_minor: Mapped[int] = mapped_column(Integer)
    happened_on: Mapped[date] = mapped_column(Date, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
