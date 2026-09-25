"""What the onboarding keeps: that its notice was sent, and when the owner last wrote."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin, UtcDateTime


class OnboardingNotice(Base, TimestampMixin):
    """The one row saying the onboarding notice reached the chat.

    Its own table, because the history folds into a Summary and a message kind for it would
    be the shell's list naming a Safwa feature. No row, no notice yet.
    """

    __tablename__ = "onboarding_notice"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)


class OwnerPresence(Base):
    """The one row saying when the owner last wrote to Safwa. No row, no message yet.

    Kept here rather than read from the chat: the owner's own messages are not registered,
    and reading the chat back through Telethon is not something a turn waits on.
    """

    __tablename__ = "owner_presence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_message_at: Mapped[datetime] = mapped_column(UtcDateTime)
