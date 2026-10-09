"""The access state, clear boundary, and messages awaiting physical delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..foundation.models import Base, TimestampMixin


@dataclass(frozen=True, slots=True)
class Open:
    pass


@dataclass(frozen=True, slots=True)
class Locked:
    pass


@dataclass(frozen=True, slots=True)
class Unlocking:
    pass


type AccessState = Open | Locked | Unlocking


class ChatClearBoundary(Base):
    __tablename__ = "chat_clear_boundaries"
    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    through_message_id: Mapped[int] = mapped_column(Integer)


class DeferredDelivery(Base, TimestampMixin):
    __tablename__ = "deferred_deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    event_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    source_note_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
