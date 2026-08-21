from __future__ import annotations

import secrets
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .constants import DIARY_TIME_DEFAULT, SPRINT_LENGTH_DAYS
from .enums import (
    ActorType,
    CardStage,
    MessageKind,
    Priority,
    ProposalStatus,
)
from .features.diary.model import DiaryEntry as DiaryEntry
from .foundation.models import Base, TimestampMixin, UtcDateTime
from .foundation.models import Workspace as Workspace


def new_correlation_id() -> str:
    """Return a short internal audit correlation key, not an entity identifier."""
    return secrets.token_hex(8)


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    about_me: Mapped[str] = mapped_column(Text, default="")
    advisor_instructions: Mapped[str] = mapped_column(Text, default="")
    capacity_effort_points: Mapped[int | None] = mapped_column(Integer)
    sprint_length_days: Mapped[int] = mapped_column(Integer, default=SPRINT_LENGTH_DAYS)
    memory_update_time: Mapped[time | None] = mapped_column(Time)
    # NULL is the Diary's off switch: `sync_diary_reminder` deletes the system Reminder
    # rather than keeping a second flag that could disagree with it.
    diary_time: Mapped[time | None] = mapped_column(
        Time, default=time.fromisoformat(DIARY_TIME_DEFAULT)
    )
    diary_instructions: Mapped[str] = mapped_column(Text, default="")


class Value(Base, TimestampMixin):
    __tablename__ = "values"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


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
    liked: Mapped[bool | None] = mapped_column(Boolean)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    parent: Mapped[Card | None] = relationship(
        remote_side="Card.id", foreign_keys=[parent_id], back_populates="children"
    )
    children: Mapped[list[Card]] = relationship(
        foreign_keys=[parent_id], back_populates="parent", cascade="all, delete-orphan"
    )
    values: Mapped[list[CardValue]] = relationship(cascade="all, delete-orphan")
    categories: Mapped[list[CardCategory]] = relationship(cascade="all, delete-orphan")
    energy_types: Mapped[list[CardEnergyType]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_cards_live_sort", "effective_stage", "hard_time", "priority", "created_at"),
    )


class CardValue(Base):
    __tablename__ = "card_values"
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    value_id: Mapped[int] = mapped_column(
        ForeignKey("values.id", ondelete="CASCADE"), primary_key=True
    )


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class CardTag(Base):
    __tablename__ = "card_tags"
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class SavedRequest(Base, TimestampMixin):
    """A user-visible, AI-authored read-only Card query."""

    __tablename__ = "saved_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    query_sql: Mapped[str] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class Check(Base, TimestampMixin):
    """One state observation: "did this hold?", answered once and then replaced.

    Pending is derived (`outcome IS NULL`), never stored, so there is no reset path.
    A resolved Check may be re-answered; the previous outcome is overwritten and lost,
    which is why `resolved_at` keeps the *first* resolution — it is the observation time
    the trend is keyed on, while `updated_at` carries any later correction.
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


class CardCheck(Base):
    """The one Check relationship, stored on the Card side like `card_values`.

    A Check is not owned by a Card: the same Check may be linked to many Cards, and one
    answer satisfies every one of them.
    """

    __tablename__ = "card_checks"
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    check_id: Mapped[int] = mapped_column(
        ForeignKey("checks.id", ondelete="CASCADE"), primary_key=True, index=True
    )


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


class Sprint(Base, TimestampMixin):
    __tablename__ = "sprints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, unique=True)
    planned_start_date: Mapped[date] = mapped_column(Date)
    planned_end_date: Mapped[date] = mapped_column(Date)
    actual_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capacity_effort_points: Mapped[int | None] = mapped_column(Integer)
    success_criteria: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    finish_reason: Mapped[str | None] = mapped_column(String(100))


class SprintCommitment(Base, TimestampMixin):
    __tablename__ = "sprint_commitments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sprint_id: Mapped[int] = mapped_column(ForeignKey("sprints.id", ondelete="CASCADE"), index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    effort_snapshot: Mapped[int] = mapped_column(Integer)
    scope_kind: Mapped[str] = mapped_column(String(20), default="initial")
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(20))

    __table_args__ = (UniqueConstraint("sprint_id", "card_id"),)


class CardEvent(Base):
    __tablename__ = "card_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int | None] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), index=True
    )
    sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprints.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(20), default=ActorType.SYSTEM.value)
    operation: Mapped[str] = mapped_column(String(80))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(16), default=new_correlation_id, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChangeProposal(Base, TimestampMixin):
    __tablename__ = "change_proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=ProposalStatus.PENDING.value)
    workspace_revision: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProposalChange(Base):
    __tablename__ = "proposal_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("change_proposals.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    entity: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    expected_version: Mapped[int | None] = mapped_column(Integer)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AgentRun(Base, TimestampMixin):
    """One model session, from its first turn to whichever turn ends it.

    A session that stops on an approval screen keeps everything it needs to continue in
    `state_json`, so it resumes from its own row.  `claimed_at` is taken before resuming
    and released afterwards: it is what stops two resumes of the same session.  A session
    suspended on a child it routed to keeps the unanswered call in `state_json` too.
    """

    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(30), default="advisor")
    # The session that routed here.  A finished session hands its receipt back up this
    # link, so a turn ends only when the root session answers.
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), index=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_message_id: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(40), default=MessageKind.DASHBOARD.value)
    related_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("chat_id", "message_id"),)


class FeedbackQueue(Base, TimestampMixin):
    __tablename__ = "feedback_queue"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), unique=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answer: Mapped[bool | None] = mapped_column(Boolean)


class SummaryState(Base):
    __tablename__ = "summary_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    summary_message_id: Mapped[int | None] = mapped_column(Integer)
    covered_message_id: Mapped[int | None] = mapped_column(Integer)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryFactCache(Base):
    __tablename__ = "memory_fact_cache"
    line_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(20), default="manual")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemorySyncState(Base):
    __tablename__ = "memory_sync_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    file_mtime: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    warning_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memory_last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Reminder(Base, TimestampMixin):
    """A trigger the owner set: instruction text plus a schedule, and nothing else.

    Deletion is the only off switch; there is no `archived_at` and no `active` flag. The
    subject is named inside `instruction` as `#id` text rather than by a foreign key, so
    one Reminder may concern any number of Safwa items of any type.

    `next_fire_at` is the only column the scheduler poll reads, and it is advanced *only*
    after an escalation succeeds, so a cancelled or crashed turn leaves the row overdue for
    the next tick to retry.
    """

    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instruction: Mapped[str] = mapped_column(Text)
    # Set only on the two Reminders a Sprint start creates, so finishing that Sprint can
    # remove them; an owner-created Reminder never carries one.
    sprint_id: Mapped[int | None] = mapped_column(
        ForeignKey("sprints.id", ondelete="CASCADE"), index=True
    )
    # Safwa's own trigger, derived from Settings: hidden from `/reminders` and from
    # `ai_reminders`, and refused by the edit and delete paths.
    system: Mapped[bool] = mapped_column(Boolean, default=False)

    schedule_kind: Mapped[str] = mapped_column(String(20))
    weekdays: Mapped[list[str]] = mapped_column(JSON, default=list)
    at_time: Mapped[time | None] = mapped_column(Time)
    # UTC.  The one-shot moment, or the moment a recurrence starts; a floor, never a rhythm.
    anchor_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    quiet_windows: Mapped[list[str]] = mapped_column(JSON, default=list)

    next_fire_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    fire_count: Mapped[int] = mapped_column(Integer, default=0)

    version: Mapped[int] = mapped_column(Integer, default=1)


class UiSession(Base, TimestampMixin):
    __tablename__ = "ui_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(30))
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CallbackToken(Base):
    __tablename__ = "callback_tokens"
    token: Mapped[str] = mapped_column(String(24), primary_key=True)
    owner_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


