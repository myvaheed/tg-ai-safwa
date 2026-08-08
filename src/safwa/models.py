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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .enums import (
    ActorType,
    CardKind,
    CardStage,
    DraftStatus,
    MessageKind,
    Priority,
    ProposalStatus,
    WorkspaceMode,
)


def new_correlation_id() -> str:
    """Return a short internal audit correlation key, not an entity identifier."""
    return secrets.token_hex(8)


class Base(DeclarativeBase):
    pass


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
    revision: Mapped[int] = mapped_column(Integer, default=1)


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    about_me: Mapped[str] = mapped_column(Text, default="")
    advisor_instructions: Mapped[str] = mapped_column(Text, default="")
    wake_time: Mapped[time | None] = mapped_column(Time)
    bed_time: Mapped[time | None] = mapped_column(Time)
    quiet_start: Mapped[time | None] = mapped_column(Time)
    quiet_end: Mapped[time | None] = mapped_column(Time)
    morning_checkin: Mapped[time | None] = mapped_column(Time)
    evening_checkin: Mapped[time | None] = mapped_column(Time)
    proactive_limit: Mapped[int] = mapped_column(Integer, default=3)
    reminder_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=180)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    weekend_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    capacity_effort_points: Mapped[int | None] = mapped_column(Integer)
    memory_update_time: Mapped[time | None] = mapped_column(Time)


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
    effort_points: Mapped[int | None] = mapped_column(Integer)
    repeatable: Mapped[bool] = mapped_column(Boolean, default=False)
    repeat_series_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_instance_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id"))
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


class CardDependency(Base, TimestampMixin):
    __tablename__ = "card_dependencies"
    blocked_card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    blocker_card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    copy_to_repeat: Mapped[bool] = mapped_column(Boolean, default=False)


class Sprint(Base, TimestampMixin):
    __tablename__ = "sprints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, unique=True)
    planned_start_date: Mapped[date] = mapped_column(Date)
    planned_end_date: Mapped[date] = mapped_column(Date)
    actual_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capacity_effort_points: Mapped[int | None] = mapped_column(Integer)
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


class CardDraftBundle(Base, TimestampMixin):
    __tablename__ = "card_draft_bundles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=DraftStatus.EDITING.value)
    active_draft_id: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CardDraft(Base, TimestampMixin):
    __tablename__ = "card_drafts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bundle_id: Mapped[int] = mapped_column(
        ForeignKey("card_draft_bundles.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"))
    expected_parent_version: Mapped[int | None] = mapped_column(Integer)
    parent_draft_id: Mapped[int | None] = mapped_column(ForeignKey("card_drafts.id"))
    root_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(20), default=CardKind.ACTION.value)
    title: Mapped[str] = mapped_column(String(500), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(20), default=CardStage.BACKLOG.value)
    priority: Mapped[str] = mapped_column(String(20), default=Priority.MEDIUM.value)
    hard_time: Mapped[bool] = mapped_column(Boolean, default=False)
    effort_points: Mapped[int | None] = mapped_column(Integer)
    repeatable: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default=DraftStatus.EDITING.value)
    field_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id"))


class DraftValue(Base):
    __tablename__ = "draft_values"
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("card_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    value_id: Mapped[int] = mapped_column(
        ForeignKey("values.id", ondelete="CASCADE"), primary_key=True
    )
    expected_version: Mapped[int] = mapped_column(Integer)


class DraftTag(Base):
    __tablename__ = "draft_tags"
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("card_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    expected_version: Mapped[int] = mapped_column(Integer)


class DraftCategory(Base):
    __tablename__ = "draft_categories"
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("card_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String(30), primary_key=True)


class DraftEnergyType(Base):
    __tablename__ = "draft_energy_types"
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("card_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    energy_type: Mapped[str] = mapped_column(String(30), primary_key=True)


class DraftDependency(Base):
    __tablename__ = "draft_dependencies"
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("card_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    blocker_card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    expected_version: Mapped[int] = mapped_column(Integer)
    copy_to_repeat: Mapped[bool] = mapped_column(Boolean, default=False)


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


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    source_message_id: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
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
    processed_message_id: Mapped[int | None] = mapped_column(Integer)
    memory_last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReminderState(Base):
    __tablename__ = "reminder_state"
    kind: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScheduledJob(Base, TimestampMixin):
    __tablename__ = "scheduled_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(50))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


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
