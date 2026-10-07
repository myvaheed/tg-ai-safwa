"""The Reminder writes, shared by the proposal handler and the `/reminders` screens.

Timing is never authored here: a resolved :class:`Schedule` arrives and the first fire is
computed from it. Editing text and changing the schedule are separate operations, which is
what keeps a reworded Reminder firing at the moment it always did.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.errors import DomainError

from ...enums import ActorType
from ...foundation.log_events import CREATE, DELETE, UPDATE, record_log_event, snapshot
from ...foundation.workspace import Workspace, bump_workspace
from .api import (
    REMINDER_CATCHUP_GRACE_MINUTES,
    Schedule,
    next_fire,
    on_wall_clock,
    roll_forward,
    schedule_columns,
    schedule_of,
)
from .model import Reminder

# The change a hook may follow up on: the owner's Reminder was created, however it was saved.
REMINDER_CREATED = "reminder.created"


async def create_reminder(
    session: AsyncSession,
    *,
    instruction: str,
    schedule: Schedule,
    tz: ZoneInfo,
    actor: ActorType = ActorType.USER_UI,
    item_type: str | None = None,
    item_id: int | None = None,
) -> Reminder:
    """Store the owner's Reminder and compute its first fire. The schedule arrives already
    resolved; an item is named when Remind on its Schedule made it."""
    clean = instruction.strip()
    if not clean:
        raise DomainError("Reminder text cannot be empty")
    reminder = Reminder(
        instruction=clean,
        next_fire_at=_first_fire(schedule, tz),
        item_type=item_type,
        item_id=item_id,
        **schedule_columns(schedule),
    )
    session.add(reminder)
    await session.flush()
    await bump_workspace(session)
    await _record(session, reminder, CREATE, actor)
    record_change(session, REMINDER_CREATED, reminder.id)
    return reminder


async def update_reminder_text(
    session: AsyncSession, reminder_id: int, instruction: str, *, actor: ActorType = ActorType.USER_UI
) -> Reminder:
    """Edit what a Reminder tells the advisor, and nothing about when it fires."""
    reminder = await _existing_reminder(session, reminder_id)
    before = snapshot(reminder)
    clean = instruction.strip()
    if not clean:
        raise DomainError("Reminder text cannot be empty")
    reminder.instruction = clean
    reminder.version += 1
    await _record(session, reminder, UPDATE, actor, before)
    await bump_workspace(session)
    return reminder


async def reschedule_reminder(
    session: AsyncSession,
    reminder_id: int,
    *,
    schedule: Schedule,
    tz: ZoneInfo,
    actor: ActorType = ActorType.USER_UI,
) -> Reminder:
    reminder = await _existing_reminder(session, reminder_id)
    before = snapshot(reminder)
    first = _first_fire(schedule, tz)
    for column, value in schedule_columns(schedule).items():
        setattr(reminder, column, value)
    reminder.next_fire_at = first
    reminder.version += 1
    await _record(session, reminder, "reschedule", actor, before)
    await bump_workspace(session)
    return reminder


async def move_reminder(session: AsyncSession, reminder_id: int, item_id: int) -> None:
    """Hand a Reminder that Remind made on to the next instance of its item's series."""
    reminder = await _existing_reminder(session, reminder_id)
    reminder.item_id = item_id


async def delete_reminder(session: AsyncSession, reminder_id: int, *, actor: ActorType = ActorType.USER_UI) -> None:
    """Remove a Reminder outright; there is no archive."""
    reminder = await _existing_reminder(session, reminder_id)
    await _record(session, reminder, DELETE, actor, snapshot(reminder))
    await session.delete(reminder)
    await bump_workspace(session)


async def _record(
    session: AsyncSession,
    reminder: Reminder,
    operation: str,
    actor: ActorType,
    before: dict[str, Any] | None = None,
) -> None:
    await record_log_event(
        session, "reminder", reminder, reminder.instruction, operation, actor, before
    )


async def _existing_reminder(session: AsyncSession, reminder_id: int) -> Reminder:
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        raise DomainError("Reminder does not exist")
    return reminder


def _first_fire(schedule: Schedule, tz: ZoneInfo) -> datetime:
    first = next_fire(schedule, previous=None, now=datetime.now(UTC), tz=tz)
    if first is None:
        raise DomainError("That schedule has no future occurrence")
    return first


async def reconcile_reminders(session: AsyncSession, now: datetime | None = None) -> None:
    """Fix `next_fire_at` on every repeating Reminder after downtime.

    Two things go stale while the process is down. A wall-clock schedule stores a local
    time, so after a timezone change "08:30" is a different UTC instant and every stored
    fire time is wrong at once; those are rebuilt. A schedule that came due meanwhile is
    rolled forward, but only once it is past the catch-up grace — inside the grace the row
    stays overdue, because the first tick firing it is the catch-up.

    A one-shot is never moved: it always fires, however late.
    """
    now = now or datetime.now(UTC)
    workspace = await session.get(Workspace, 1)
    tz = ZoneInfo(workspace.timezone if workspace else "UTC")
    grace = timedelta(minutes=REMINDER_CATCHUP_GRACE_MINUTES)
    for reminder in await session.scalars(select(Reminder)):
        schedule = schedule_of(reminder)
        if not schedule.repeating:
            continue  # a one-shot always fires, however late
        if not on_wall_clock(schedule, reminder.next_fire_at, tz):
            rebuilt = next_fire(schedule, previous=None, now=now, tz=tz)
            if rebuilt is not None:
                reminder.next_fire_at = rebuilt
        if now - reminder.next_fire_at > grace:
            reminder.next_fire_at = roll_forward(
                schedule, previous=reminder.next_fire_at, now=now, tz=tz
            )
