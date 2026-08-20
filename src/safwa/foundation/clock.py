"""The seam that lets a use case be told what time it is instead of asking the process.

Business rules that read the wall clock cannot be tested without waiting or patching, so
time arrives as a dependency.  `domain.utcnow` is the pre-refactoring caller of the same
system clock and is replaced feature by feature.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current moment, always timezone-aware and always in UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
