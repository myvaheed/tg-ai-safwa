"""The access state, and snapshots of hidden unanswered messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..foundation.models import Base


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


class DeferredDelivery(Base):
    __tablename__ = "deferred_deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    source_note_id: Mapped[int] = mapped_column(Integer, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
