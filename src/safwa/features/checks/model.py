"""The Check: one state observation, and the row that records it."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin


class CheckOutcome(StrEnum):
    """The two settable answers to a Check. Pending is derived from a null outcome."""

    PASSED = "passed"
    MISSED = "missed"


CHECK_OUTCOME_LABELS = {
    "pending": "Pending",
    CheckOutcome.PASSED.value: "Passed",
    CheckOutcome.MISSED.value: "Missed",
}
# A Check is answered with the Card lifecycle verbs: complete is Passed, cancel is Missed.
CHECK_ANSWER_ACTIONS = {
    "complete": CheckOutcome.PASSED.value,
    "cancel": CheckOutcome.MISSED.value,
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
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(20))
    series_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("checks.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
