"""The Card and the rows only a Card owns.

`Card.values` names `CardValue` without importing it: the link row belongs to Values, and
SQLAlchemy resolves the target from the registry, so the Card side of a shared aggregate
costs no dependency on another feature.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    Select,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ...foundation.models import Base, TimestampMixin, UtcDateTime


class CardKind(StrEnum):
    GOAL = "goal"
    SUBGOAL = "subgoal"
    ACTION = "action"


# What one rung costs: how the owner will be able to carry on afterwards, and what
# recovery it takes first.  One axis, and the only one that reads the same for a
# physical, a cognitive and an emotional load — which of those it is, `EnergyType`
# already carries.  The question the rung answers is how much the whole thing takes in
# the owner's usual state; today's tiredness decides how many things they take on, not
# what one of them costs.
EFFORT_RUNGS: dict[float, str] = {
    0.5: "done in passing, the load is barely noticed",
    1: "done, and the day goes on as it was",
    2: "a little tired, but able to carry on without a rest",
    3: "able to carry on only after a break",
    5: "after a full rest there is enough for one more serious thing",
    8: "only light work is left for today",
    13: "nothing is left for anything else today",
}
# The allowed Action effort scale.  Mirrored by the Literal in cards/agent.py.
EFFORT_POINTS = frozenset(EFFORT_RUNGS)


def effort_label(points: float | None) -> str:
    """0.5 keeps its half; every other rung reads as the whole number it is."""
    return "—" if points is None else f"{points:g}"


class Priority(StrEnum):
    CRITICAL = "critical"
    MEDIUM = "medium"
    LOW = "low"


class Category(StrEnum):
    SELF = "self"
    CONTRIBUTION = "contribution"
    WORK = "work"
    REST = "rest"


class EnergyType(StrEnum):
    PHYSICAL = "physical"
    COGNITIVE = "cognitive"
    SOCIAL = "social"
    VALUES = "values"


class CardStage(StrEnum):
    BACKLOG = "backlog"
    SPRINT = "sprint"
    TODAY = "today"
    DONE = "done"


TERMINAL_STAGES = {CardStage.DONE}
LIVE_STAGE_PRECEDENCE = {
    CardStage.BACKLOG: 1,
    CardStage.SPRINT: 2,
    CardStage.TODAY: 3,
}


def new_correlation_id() -> str:
    """Return a short internal audit correlation key, not an entity identifier."""
    return secrets.token_hex(8)


class Card(Base, TimestampMixin):
    __tablename__ = "cards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500))
    note: Mapped[str] = mapped_column(Text, default="")
    manual_stage: Mapped[str] = mapped_column(String(20), default=CardStage.BACKLOG.value)
    effective_stage: Mapped[str] = mapped_column(
        String(20), default=CardStage.BACKLOG.value, index=True
    )
    priority: Mapped[str] = mapped_column(String(20), default=Priority.MEDIUM.value)
    hard_time: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_description: Mapped[str] = mapped_column(Text, default="")
    effort_points: Mapped[float | None] = mapped_column(Float)
    repeatable: Mapped[bool] = mapped_column(Boolean, default=False)
    repeat_series_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("cards.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    archived_at: Mapped[datetime | None] = mapped_column(UtcDateTime, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    parent: Mapped[Card | None] = relationship(
        remote_side="Card.id", foreign_keys=[parent_id], back_populates="children"
    )
    children: Mapped[list[Card]] = relationship(
        foreign_keys=[parent_id], back_populates="parent", cascade="all, delete-orphan"
    )
    # Quoted on purpose: the name is resolved from the mapper registry, and importing it
    # would make Cards depend on the Values package for a link row it owns the other half of.
    values: Mapped[list["CardValue"]] = relationship(  # noqa: F821, UP037
        "CardValue", cascade="all, delete-orphan"
    )
    categories: Mapped[list[CardCategory]] = relationship(cascade="all, delete-orphan")
    energy_types: Mapped[list[CardEnergyType]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_cards_live_sort", "effective_stage", "hard_time", "priority", "created_at"),
    )

    def is_closed_repeat(self) -> bool:
        """A repeat instance that already ended, so its series continues on a newer row."""
        return self.repeatable and CardStage(self.effective_stage) in TERMINAL_STAGES

    def live_instance_query(self) -> Select[tuple[int]]:
        """The open Card of this series. Only the newest instance can be open."""
        return (
            select(Card.id)
            .where(
                Card.repeat_series_id == (self.repeat_series_id or self.id),
                Card.effective_stage.notin_([stage.value for stage in TERMINAL_STAGES]),
            )
            .order_by(Card.id.desc())
            .limit(1)
        )

    def series_done_since_query(self, since: datetime) -> Select[tuple[int]]:
        """Anything in this series completed at or after `since`, if there is one."""
        return (
            select(Card.id)
            .where(
                Card.repeat_series_id == (self.repeat_series_id or self.id),
                Card.completed_at >= since,
            )
            .limit(1)
        )

    def series_index_query(self) -> Select[tuple[int]]:
        """This instance's place, counted over every row the series has ever had."""
        return (
            select(func.count())
            .select_from(Card)
            .where(
                Card.repeat_series_id == (self.repeat_series_id or self.id),
                Card.id <= self.id,
            )
        )


class CardCheck(Base):
    """The one Check relationship, stored on the Card side like `card_values`.

    A Check hangs on one Card or on none, which is what the unique constraint says: the
    link is written from the Card and recorded in that Card's event log.
    """

    __tablename__ = "card_checks"
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    check_id: Mapped[int] = mapped_column(
        ForeignKey("checks.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    __table_args__ = (UniqueConstraint("check_id"),)


class CardCategory(Base):
    __tablename__ = "card_categories"
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String(30), primary_key=True)


class CardEnergyType(Base):
    __tablename__ = "card_energy_types"
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    energy_type: Mapped[str] = mapped_column(String(30), primary_key=True)

class CardEvent(Base):
    __tablename__ = "card_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int | None] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), index=True
    )
    sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprints.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(20))
    operation: Mapped[str] = mapped_column(String(80))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(16), default=new_correlation_id, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())

