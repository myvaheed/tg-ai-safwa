"""Safwa's own schema root.

The shell declares its six tables on a `Base` of its own, and an application declares its
tables on this one. Two applications can then run in one process without either one's
`create_all` reaching the other's tables — which is what `tests/shell/` checks by building
a second application and counting what a fresh database gets.

The column types and the timestamp pair are the shell's and are re-exported, because they
say how a row is written rather than which application owns it.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from tg_agent_shell.foundation.models import TimestampMixin, UtcDateTime


class Base(DeclarativeBase):
    """Every Safwa table hangs here, and nothing else does."""


__all__ = ["Base", "TimestampMixin", "UtcDateTime"]
