"""How a clock the owner typed is read, and the schedule a Card borrows.

A door carries the vocabulary and what needs no operation. A Reminder another feature asks
for is written at the operations layer, in `use_cases.py`.
"""

from __future__ import annotations

from datetime import time

from .agent import resolve_schedule
from .schedule import (
    Schedule,
    ScheduleError,
    describe,
    next_fire,
    parse_clock,
    parse_phrase,
    roll_forward,
    schedule_from_payload,
    schedule_payload,
)

__all__ = [
    "Schedule",
    "ScheduleError",
    "describe",
    "next_fire",
    "parse_clock",
    "parse_clock_or_off",
    "parse_phrase",
    "resolve_schedule",
    "roll_forward",
    "schedule_from_payload",
    "schedule_payload",
]


def parse_clock_or_off(raw: str) -> time | None:
    """A daily Profile clock. ``off`` is None, which is how that field is switched off."""
    return None if raw.strip().lower() == "off" else parse_clock(raw)
