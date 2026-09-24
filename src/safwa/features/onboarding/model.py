"""What the onboarding keeps: that its notice was sent."""

from __future__ import annotations

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from ...foundation.models import Base, TimestampMixin


class OnboardingNotice(Base, TimestampMixin):
    """The one row saying the onboarding notice reached the chat.

    Its own table, because the history folds into a Summary and a message kind for it would
    be the shell's list naming a Safwa feature. No row, no notice yet.
    """

    __tablename__ = "onboarding_notice"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
