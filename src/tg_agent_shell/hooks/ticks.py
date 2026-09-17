"""When a daily check comes due: the workspace's clock passed the time its reader names.

The last look is kept in process memory, and starting is the first look: a time that
passed while Safwa was down is not run late, and a day of many polls runs its check once.
The times themselves are handed to every look, so one the owner moves counts from the next
time it passes.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .contracts import Tick, TickTime


def last_passing(at: time, *, now: datetime, tz: ZoneInfo) -> datetime:
    """The most recent moment the local clock read `at`: today's, or else yesterday's."""
    local = now.astimezone(tz)
    moment = datetime.combine(local.date(), at, tzinfo=tz)
    return moment if moment <= local else moment - timedelta(days=1)


class TickSchedule:
    def __init__(self, *, now: datetime, tz: ZoneInfo) -> None:
        self.tz = tz
        self.looked = now

    def due(self, now: datetime, clocks: Mapping[TickTime, time]) -> list[Tick]:
        """A Tick per daily time that passed since the last look; this look is the last one now."""
        ticks = [
            Tick(at.strftime("%H:%M"), clock)
            for clock, at in clocks.items()
            if self.looked < last_passing(at, now=now, tz=self.tz)
        ]
        self.looked = now
        return ticks
