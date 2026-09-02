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
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ...enums import Priority
from ...foundation.models import Base, TimestampMixin, UtcDateTime


class CardStage(StrEnum):
    BACKLOG = "backlog"
    SPRINT = "sprint"
    TODAY = "today"
    DONE = "done"
    CANCELLED = "cancelled"


TERMINAL_STAGES = {CardStage.DONE, CardStage.CANCELLED}
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
    effort_points: Mapped[int | None] = mapped_column(Integer)
    repeatable: Mapped[bool] = mapped_column(Boolean, default=False)
    repeat_series_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("cards.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
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


def is_closed_repeat(card: Card) -> bool:
    """A repeat instance that already ended, so its series continues on a newer row."""
    return card.repeatable and CardStage(card.effective_stage) in TERMINAL_STAGES
