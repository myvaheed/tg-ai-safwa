"""When a Card must happen: a Reminder's schedule, carried by the Card.

The schedule is resolved the way a Reminder's is — by hand from a short phrase, by the
model from plain words — and stored as the same JSON payload. `hard_time_at` is its next
occurrence, worked out with the Reminders' own arithmetic, and is what a list sorts by.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError

from ...foundation.workspace import require_workspace
from ..reminders.api import (
    Schedule,
    ScheduleError,
    describe,
    next_fire,
    parse_phrase,
    roll_forward,
    schedule_from_payload,
    schedule_payload,
)

HARD_TIME_INSTRUCTION = (
    "Send when it must happen: 15:00, 20.09.2026 15:00, Mon Wed 09:00, or daily 09:00. "
    "Send off to clear it."
)


@dataclass(frozen=True, slots=True)
class HardTime:
    schedule: Schedule
    # UTC: the moment itself, or the next occurrence of a repeating one.
    at: datetime


def hard_time_columns(hard_time: HardTime | None) -> dict[str, Any]:
    """The two Card columns a Hard Time occupies."""
    if hard_time is None:
        return {"hard_time": None, "hard_time_at": None}
    return {"hard_time": schedule_payload(hard_time.schedule), "hard_time_at": hard_time.at}


async def workspace_zone(session: AsyncSession) -> ZoneInfo:
    return ZoneInfo((await require_workspace(session)).timezone)


def _due(schedule: Schedule, tz: ZoneInfo) -> HardTime:
    at = next_fire(schedule, previous=None, now=utcnow(), tz=tz)
    if at is None:
        raise DomainError("That Hard Time has no occurrence")
    return HardTime(schedule, at)


async def resolve_hard_time(
    session: AsyncSession, payload: dict[str, Any] | None
) -> HardTime | None:
    """A stored schedule as the Hard Time it is now: due at its next occurrence."""
    if payload is None:
        return None
    return _due(schedule_from_payload(payload), await workspace_zone(session))


async def typed_hard_time(session: AsyncSession, raw: str) -> HardTime | None:
    """What the owner typed into the Hard Time prompt. `off` clears it."""
    if raw.strip().lower() == "off":
        return None
    tz = await workspace_zone(session)
    try:
        return _due(parse_phrase(raw, now=utcnow(), tz=tz), tz)
    except ScheduleError as error:
        raise DomainError(str(error)) from error


async def following_hard_time(session: AsyncSession, card: Any) -> HardTime | None:
    """The Hard Time a repeat successor takes: the next occurrence of a repeating schedule.

    A Hard Time that was one moment does not carry over: the next instance is a new day,
    and nothing has fixed its time yet.
    """
    if card.hard_time is None:
        return None
    schedule = schedule_from_payload(card.hard_time)
    if not schedule.repeating:
        return None
    tz = await workspace_zone(session)
    return HardTime(
        schedule, roll_forward(schedule, previous=card.hard_time_at, now=utcnow(), tz=tz)
    )


def hard_time_text(payload: dict[str, Any] | None, *, tz: ZoneInfo) -> str | None:
    """The schedule in words, as a Reminder's reads."""
    if payload is None:
        return None
    return describe(schedule_from_payload(payload), tz=tz, now=utcnow())
