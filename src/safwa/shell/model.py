"""The two rows the shell owns: a draft being edited, and a button that may be pressed once.

Neither belongs to a feature. A `UiSession` is whatever editor the owner has open, and a
`CallbackToken` is one press on one inline button, both of which die with the run of Safwa
that drew them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..foundation.models import Base, TimestampMixin


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
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
