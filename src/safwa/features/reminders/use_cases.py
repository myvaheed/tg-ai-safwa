"""The Reminder writes, shared by the proposal handler and the `/reminders` screens.

Timing is never authored here: a resolved :class:`Schedule` arrives and the first fire is
computed from it. Editing text and changing the schedule are separate operations, which is
what keeps a reworded Reminder firing at the moment it always did.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.errors import DomainError

from ...enums import ActorType
from ...foundation.log_events import CREATE, DELETE, UPDATE, record_log_event, snapshot
from ...foundation.workspace import Workspace, bump_workspace
from .model import Reminder, ScheduleKind
from .schedule import (
    REMINDER_CATCHUP_GRACE_MINUTES,
    Schedule,
    next_fire,
    on_wall_clock,
    roll_forward,
    schedule_columns,
    schedule_of,
)

# The change a hook may follow up on: the owner's Reminder was created, however it was saved.
# A Sprint's own warning is Safwa's, and is stored without it.
REMINDER_CREATED = "reminder.created"


async def create_reminder(
    session: AsyncSession,
    *,
    instruction: str,
    schedule: Schedule,
    tz: ZoneInfo,
    actor: ActorType = ActorType.USER_UI,
) -> Reminder:
    """Store the owner's Reminder and compute its first fire. The schedule arrives already
    resolved."""
    reminder = await _store_reminder(session, instruction=instruction, schedule=schedule, tz=tz)
    await _record(session, reminder, CREATE, actor)
    record_change(session, REMINDER_CREATED, reminder.id)
    return reminder


async def _store_reminder(
    session: AsyncSession, *, instruction: str, schedule: Schedule, tz: ZoneInfo
) -> Reminder:
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
    session: AsyncSession, reminder_id: int, instruction: str, *, actor: ActorType = ActorType.USER_UI
) -> Reminder:
    """Edit what a Reminder tells the advisor, and nothing about when it fires."""
    reminder = await _editable_reminder(session, reminder_id)
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
    reminder = await _editable_reminder(session, reminder_id)
    before = snapshot(reminder)
    first = _first_fire(schedule, tz)
    for column, value in schedule_columns(schedule).items():
        setattr(reminder, column, value)
    reminder.next_fire_at = first
    reminder.version += 1
    await _record(session, reminder, "reschedule", actor, before)
    await bump_workspace(session)
    return reminder


async def delete_reminder(session: AsyncSession, reminder_id: int, *, actor: ActorType = ActorType.USER_UI) -> None:
    """Remove a Reminder outright; there is no archive."""
    reminder = await _editable_reminder(session, reminder_id)
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


async def _editable_reminder(session: AsyncSession, reminder_id: int) -> Reminder:
    """A Reminder the owner and the advisor may touch — never one Safwa derived."""
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        raise DomainError("Reminder does not exist")
    if reminder.system:
        raise DomainError("That Reminder belongs to Safwa; change it in the Profile")
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
    stays overdue, because the first poll firing it is the catch-up.

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


# The running Sprint's own warnings; one Sprint runs at a time, so one key finds them.
SPRINT_KEY = "sprint"


async def create_sprint_reminder(
    session: AsyncSession,
    *,
    instruction: str,
    at_time: time,
    anchor_at: datetime,
    tz: ZoneInfo,
) -> Reminder:
    """One warning a Sprint sets for itself: fires once, at the clock the Sprint started at.

    No owner set it, so it is Safwa's: hidden from `/reminders` and from the model, and
    removed by the Sprint ending rather than by hand.
    """
    reminder = await _store_reminder(
        session,
        instruction=instruction,
        schedule=Schedule(kind=ScheduleKind.ONCE, at_time=at_time, anchor_at=anchor_at),
        tz=tz,
    )
    reminder.system = True
    reminder.system_key = SPRINT_KEY
    return reminder


async def delete_sprint_reminders(session: AsyncSession) -> None:
    """A finished Sprint's own warnings have nothing left to announce."""
    await session.execute(delete(Reminder).where(Reminder.system_key == SPRINT_KEY))
