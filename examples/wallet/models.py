"""This application's schema root, which is not the shell's and not Safwa's.

`create_all` walks one metadata, so an application that declares its tables here gets its
tables and the shell's, and never another application's.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from tg_agent_shell.foundation.models import TimestampMixin


class Base(DeclarativeBase):
    """Every wallet, category, entry and the ledger row itself."""


__all__ = ["Base", "TimestampMixin"]
