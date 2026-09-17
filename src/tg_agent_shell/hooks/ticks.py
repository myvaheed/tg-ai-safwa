"""When the daily check comes due: the workspace's clock passed the time the application names.

The last look is kept in process memory, and starting is the first look: a time that
passed while Safwa was down is not run late, and a day of many polls runs its check once.
The time itself is handed to every look, so one the owner moves counts from the next time
it passes.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .contracts import Tick


def last_passing(at: time, *, now: datetime, tz: ZoneInfo) -> datetime:
    """The most recent moment the local clock read `at`: today's, or else yesterday's."""
    local = now.astimezone(tz)
    moment = datetime.combine(local.date(), at, tzinfo=tz)
    return moment if moment <= local else moment - timedelta(days=1)


class TickSchedule:
    def __init__(self, *, now: datetime, tz: ZoneInfo) -> None:
        self.tz = tz
        self.looked = now

    def due(self, now: datetime, at: time) -> Tick | None:
        """The Tick when `at` passed since the last look; this look is the last one now."""
        passed = self.looked < last_passing(at, now=now, tz=self.tz)
        self.looked = now
        return Tick(at.strftime("%H:%M")) if passed else None
