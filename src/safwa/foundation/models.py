"""The schema primitives every Safwa table is built on."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Dialect, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class UtcDateTime(TypeDecorator[datetime]):
    """A ``DateTime`` that always reads back as tz-aware UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

