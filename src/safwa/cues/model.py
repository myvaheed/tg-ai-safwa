"""The durable Cue."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..foundation.models import Base, TimestampMixin


class Cue(Base, TimestampMixin):
    """One thing to say to the owner, written by whoever had the facts.

    A row exists only for a producer that cannot say it again: a Reminder retries from its
    own `next_fire_at`, while a Sprint ends once and has nothing to fire twice. The row is
    deleted when the turn lands, so a failed or cancelled turn leaves it for the next poll.
    """

    __tablename__ = "cues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=lambda: uuid4().hex
    )
    text: Mapped[str] = mapped_column(Text)
