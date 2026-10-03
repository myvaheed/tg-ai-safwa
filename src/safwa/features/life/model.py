"""What Life in weeks keeps of its own: the owner's birth date and how many years its grid
holds."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin

# How many years the grid holds out of the box, and what Settings accepts.
LIFE_YEARS_DEFAULT = 90
LIFE_YEARS_MIN = 50
LIFE_YEARS_MAX = 120


class LifeSettings(Base, TimestampMixin):
    """The one row of the Life settings. With no row, no birth date is set yet."""

    __tablename__ = "life_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    birth_date: Mapped[date | None] = mapped_column(Date)
    years: Mapped[int] = mapped_column(Integer, default=LIFE_YEARS_DEFAULT)
