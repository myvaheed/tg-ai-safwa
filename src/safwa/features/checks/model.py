"""The Check: one state observation, and the row that records it."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Integer, Select, String, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from tg_agent_shell.foundation.models import Base, TimestampMixin, UtcDateTime


class CheckOutcome(StrEnum):
    """The two settable answers to a Check. Pending is derived from a null outcome."""

    PASSED = "passed"
    MISSED = "missed"


CHECK_OUTCOME_LABELS = {
    "pending": "Pending",
    CheckOutcome.PASSED.value: "Passed",
    CheckOutcome.MISSED.value: "Missed",
}

class Check(Base, TimestampMixin):
    """One state observation: "did this hold?", answered once and then replaced.

    Pending is derived (`outcome IS NULL`), never stored, so the only path back to it is
    reopening the Card the Check is on.  A resolved Check may be re-answered; the previous
    outcome is overwritten and lost, which is why `resolved_at` keeps the *first*
    resolution — it is the observation time the trend is keyed on, while `updated_at`
    carries any later correction.
    """

    __tablename__ = "checks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500))
    repeatable: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[str | None] = mapped_column(String(20), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    resolved_by: Mapped[str | None] = mapped_column(String(20))
    series_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("checks.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(UtcDateTime, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    def is_closed_repeat(self) -> bool:
        """A repeat instance that already ended, so its series continues on a newer row."""
        return self.repeatable and self.outcome is not None

    def live_instance_query(self) -> Select[tuple[int]]:
        """The open Check of this series. Only the newest instance can be open."""
        return (
            select(Check.id)
            .where(
                Check.series_id == (self.series_id or self.id),
                Check.outcome.is_(None),
            )
            .order_by(Check.id.desc())
            .limit(1)
        )

    def series_index_query(self) -> Select[tuple[int]]:
        """This instance's place, counted over every row the series has ever had."""
        return (
            select(func.count())
            .select_from(Check)
            .where(Check.series_id == (self.series_id or self.id), Check.id <= self.id)
        )

