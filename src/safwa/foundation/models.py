"""Schema primitives and the workspace shared by every Safwa feature."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Dialect, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

from ..enums import WorkspaceMode


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspace"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    owner_telegram_id: Mapped[int] = mapped_column(Integer, unique=True)
    mode: Mapped[str] = mapped_column(String(20), default=WorkspaceMode.PLANNING.value)
    active_sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprints.id"))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Istanbul")
    # What the next Sprint is meant to achieve, edited during Planning and copied into the
    # Sprint at start. It outlives a Sprint so the next one can start from the last wording.
    sprint_success_criteria: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
