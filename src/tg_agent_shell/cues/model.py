"""The durable Cue."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..foundation.models import Base, TimestampMixin


class Cue(Base, TimestampMixin):
    """One thing to say to the owner, written by whoever had the facts.

    A row exists only for a producer that cannot say it again: a Reminder retries from its
    own `next_fire_at`, while a Sprint ends once and has nothing to fire twice. A row is
    written once and never edited: the poll stamps the rows one turn says with that turn's
    `event_id` just before it starts, and deletes exactly the stamped rows once the answer
    reached the owner. A failed or cancelled turn leaves them for the next poll, and a row
    written while the turn ran carries no stamp, so it is never settled with it.

    A hook's request carries no words: `hook` names it and `payload` holds what it refers
    to, and the words are made from what is still there when the row is next in line. A
    hook that fires again writes a row of its own; one turn words every row of a hook as
    one request.
    """

    __tablename__ = "cues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The turn this row is being said with — the id its message is registered under — and
    # nothing while it waits.
    event_id: Mapped[str | None] = mapped_column(String(32), index=True)
    text: Mapped[str | None] = mapped_column(Text)
    hook: Mapped[str | None] = mapped_column(String(80), index=True)
    payload: Mapped[list[Any] | None] = mapped_column(JSON)
