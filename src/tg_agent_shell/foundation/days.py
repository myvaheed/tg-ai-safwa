"""Dates as a model is told them: the month by name, and how far the day is from today.

A small model misreads date arithmetic and a month by its number, so every date it is told
says both, counted here rather than by the model (WS-CONTEXT-014).
"""

from __future__ import annotations

from datetime import date, datetime

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def day_name(day: date) -> str:
    """'21 October 2026'."""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def from_today(day: date, today: date) -> str:
    """'today', 'yesterday', 'tomorrow', '6 days ago' or 'in 6 days'."""
    days = (day - today).days
    named = {0: "today", -1: "yesterday", 1: "tomorrow"}.get(days)
    if named:
        return named
    return f"{-days} days ago" if days < 0 else f"in {days} days"


def day_words(day: date, today: date) -> str:
    """'21 October 2026 (6 days ago)'."""
    return f"{day_name(day)} ({from_today(day, today)})"


def moment_words(local: datetime, today: date) -> str:
    """A local moment: '21 October 2026 14:00 (6 days ago)'."""
    return f"{day_name(local.date())} {local:%H:%M} ({from_today(local.date(), today)})"


def today_line(local: datetime, timezone: str) -> str:
    """The one line that says which day it is, with the date as SQL compares it."""
    return (
        f"Today is {WEEKDAYS[local.weekday()]}, {day_name(local.date())} "
        f"({local.date().isoformat()}), {local:%H:%M}, {timezone}."
    )
