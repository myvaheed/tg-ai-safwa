"""One immutable source revision, with its compiled rule or clarification."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, UtcDateTime


class ScheduleDefinition(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(10))
    source_text: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(UtcDateTime)
    valid_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    rule: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    question: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "ix_schedules_current",
            "type",
            "entity_id",
            unique=True,
            sqlite_where=text("valid_until IS NULL"),
        ),
    )
