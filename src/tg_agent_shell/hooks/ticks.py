"""When a daily check comes due: the workspace's clock passed the time its subscription names.

The last look is kept in process memory, and starting is the first look: a time that
passed while Safwa was down is not run late, and a day of many polls runs its check once.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .contracts import Tick


def last_passing(at: str, *, now: datetime, tz: ZoneInfo) -> datetime:
    """The most recent moment the local clock read `at`: today's, or else yesterday's."""
    local = now.astimezone(tz)
    moment = datetime.combine(local.date(), time.fromisoformat(at), tzinfo=tz)
    return moment if moment <= local else moment - timedelta(days=1)


class TickSchedule:
    def __init__(self, times: Iterable[str], *, now: datetime, tz: ZoneInfo) -> None:
        self.times = tuple(sorted(set(times)))
        self.tz = tz
        self.looked = now

    def due(self, now: datetime) -> list[Tick]:
        """The checks whose time passed since the last look; this look is the last one now."""
        due = [
            Tick(at) for at in self.times if self.looked < last_passing(at, now=now, tz=self.tz)
        ]
        self.looked = now
        return due
