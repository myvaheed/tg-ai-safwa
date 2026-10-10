"""A committed change, recorded by the operation that made it and read after the commit.

An operation writes the fact beside its own transaction and commits nothing itself. The
one `after_commit` listener in `cues/initiatives.py` hands the facts on; a rollback drops
them. Every path that saves — a proposal, a screen — calls the same operation, so the fact
is written once, where the change is made, and never guessed from a tool name or a message.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

CHANGES = "committed"


@dataclass(frozen=True, slots=True)
class Committed:
    """What kind of thing changed, and which one."""

    kind: str
    subject_id: int
    data: Mapping[str, Any] | None = None


def record_change(
    session: AsyncSession, kind: str, subject_id: int, *, data: Mapping[str, Any] | None = None
) -> None:
    """Note a change for whoever listens after this transaction commits."""
    session.info.setdefault(CHANGES, []).append(Committed(kind, subject_id, data))


def take_changes(info: dict) -> list[Committed]:
    """Everything recorded on this session, and the session forgets it."""
    return info.pop(CHANGES, [])
