from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .cues.model import Cue as Cue
from .enums import (
    MessageKind,
)
from .features.cards.model import Card as Card
from .features.cards.model import CardCategory as CardCategory
from .features.cards.model import CardCheck as CardCheck
from .features.cards.model import CardEnergyType as CardEnergyType
from .features.cards.model import CardEvent as CardEvent
from .features.cards.model import new_correlation_id as new_correlation_id
from .features.checks.model import Check as Check
from .features.continuity.model import MemoryFactCache as MemoryFactCache
from .features.continuity.model import MemorySyncState as MemorySyncState
from .features.continuity.model import SummaryState as SummaryState
from .features.diary.model import DiaryEntry as DiaryEntry
from .features.planning.model import Sprint as Sprint
from .features.planning.model import SprintCommitment as SprintCommitment
from .features.profile.model import UserProfile as UserProfile
from .features.reminders.model import Reminder as Reminder
from .features.saved_requests.model import SavedRequest as SavedRequest
from .features.tags.model import CardTag as CardTag
from .features.tags.model import Tag as Tag
from .features.values.model import CardValue as CardValue
from .features.values.model import CheckValue as CheckValue
from .features.values.model import Value as Value
from .foundation.models import Base, TimestampMixin
from .foundation.models import Workspace as Workspace


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


