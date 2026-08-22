"""The Reminder writes, shared by the proposal handler and the `/reminders` screens.

Timing is never authored here: a resolved :class:`Schedule` arrives and the first fire is
computed from it. Editing text and changing the schedule are separate operations, which is
what keeps a reworded Reminder firing at the moment it always did.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.errors import DomainError
from ...foundation.workspace import bump_workspace
from .model import Reminder
from .schedule import Schedule, next_fire, schedule_columns


async def create_reminder(
    session: AsyncSession, *, instruction: str, schedule: Schedule, tz: ZoneInfo
) -> Reminder:
    """Store a Reminder and compute its first fire. The schedule arrives already resolved."""
    clean = instruction.strip()
    if not clean:
        raise DomainError("Reminder text cannot be empty")
    reminder = Reminder(
        instruction=clean, next_fire_at=_first_fire(schedule, tz), **schedule_columns(schedule)
    )
    session.add(reminder)
    await session.flush()
    await bump_workspace(session)
    return reminder


async def update_reminder_text(
    session: AsyncSession, reminder_id: int, instruction: str
) -> Reminder:
    """Edit what a Reminder tells the advisor, and nothing about when it fires."""
    reminder = await _editable_reminder(session, reminder_id)
    clean = instruction.strip()
    if not clean:
        raise DomainError("Reminder text cannot be empty")
    reminder.instruction = clean
    reminder.version += 1
    await bump_workspace(session)
    return reminder


async def reschedule_reminder(
    session: AsyncSession, reminder_id: int, *, schedule: Schedule, tz: ZoneInfo
) -> Reminder:
    reminder = await _editable_reminder(session, reminder_id)
    first = _first_fire(schedule, tz)
    for column, value in schedule_columns(schedule).items():
        setattr(reminder, column, value)
    reminder.next_fire_at = first
    reminder.version += 1
    await bump_workspace(session)
    return reminder


async def delete_reminder(session: AsyncSession, reminder_id: int) -> None:
    """Remove a Reminder outright; there is no archive."""
    reminder = await _editable_reminder(session, reminder_id)
    await session.delete(reminder)
    await bump_workspace(session)


async def _editable_reminder(session: AsyncSession, reminder_id: int) -> Reminder:
    """A Reminder the owner and the advisor may touch — never one Safwa derived."""
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        raise DomainError("Reminder does not exist")
    if reminder.system:
        raise DomainError("That Reminder belongs to Safwa; change it in Settings")
    return reminder


def _first_fire(schedule: Schedule, tz: ZoneInfo) -> datetime:
    first = next_fire(schedule, previous=None, now=datetime.now(UTC), tz=tz)
    if first is None:
        raise DomainError("That schedule has no future occurrence")
    return first
