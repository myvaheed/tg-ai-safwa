"""The two lists an entry is written against."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..models import Base, TimestampMixin


class CategoryKind(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class Wallet(Base, TimestampMixin):
    """One place money sits. Its balance is never stored — it is the entries summed."""

    __tablename__ = "wallets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    # ISO 4217, uppercased on the way in. Two wallets may share one.
    currency: Mapped[str] = mapped_column(String(3))


class Category(Base, TimestampMixin):
    """What an entry is for, and which way the money went."""

    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[str] = mapped_column(String(10))
