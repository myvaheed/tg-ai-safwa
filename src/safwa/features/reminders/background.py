"""The Reminder poll: the alarm clock, and what one tick is allowed to change.

The poll *is* the alarm clock — there is no scheduling library and no in-memory timer set.
``Reminder.next_fire_at`` says **when**, and nothing else: a tick writes the words down as a
Cue and moves the Reminder on in the same transaction.  What guarantees the owner gets them
is the Cue row, which is deleted only once the turn landed — so this module holds no gate,
takes no lease and runs no Advisor turn.

One thing waits to be said at a time: a tick that finds a Cue still waiting writes nothing,
and the due Reminders it would have carried stay due for the tick after that.  That is what
stops an hour of a busy owner turning into twelve messages the moment they are free.

Do not confuse the two intervals.  The poll is ``SCHEDULER_POLL_SECONDS`` and is the
system's clock; a Reminder's own ``interval_minutes`` is a property of its row.  A deferred
Reminder waits seconds for a quiet moment, never one of its own cycles.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_agent_shell.cues.queue import add_cue, next_cue

from ...constants import (
    REMINDER_CATCHUP_GRACE_MINUTES,
    REMINDER_FIRE_BATCH,
    SCHEDULER_POLL_SECONDS,
)
from .model import Reminder
from .schedule import describe, roll_forward, schedule_of

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Firing:
    """One due Reminder, resolved and ready to be written into the Cue."""

    reminder_id: int
    instruction: str
    schedule: str
    due_at: datetime


async def due_reminders(
    session: AsyncSession, *, now: datetime, limit: int = REMINDER_FIRE_BATCH
) -> list[Reminder]:
    return list(
        await session.scalars(
            select(Reminder)
            .where(Reminder.next_fire_at <= now)
            .order_by(Reminder.next_fire_at)
            .limit(limit)
        )
    )


def is_stale(reminder: Reminder, *, now: datetime) -> bool:
    """Whether a *repeating* Reminder is so overdue that firing it would be noise.

    A weekend offline must not produce 32 posture messages, so anything past the grace
    window rolls forward silently.  A one-shot is never stale: it always fires, however
    late, and the Cue says how late.
    """
    if not schedule_of(reminder).repeating:
        return False
    return now - reminder.next_fire_at > timedelta(minutes=REMINDER_CATCHUP_GRACE_MINUTES)


async def prepare(
    session: AsyncSession,
    reminders: list[Reminder],
    *,
    now: datetime,
    tz: ZoneInfo,
) -> list[Firing]:
    """Resolve each due Reminder into a Firing, rolling stale repeats forward instead."""
    firings: list[Firing] = []
    for reminder in reminders:
        schedule = schedule_of(reminder)
        if is_stale(reminder, now=now):
            reminder.next_fire_at = roll_forward(
                schedule, previous=reminder.next_fire_at, now=now, tz=tz
            )
            continue
        firings.append(
            Firing(
                reminder_id=reminder.id,
                instruction=reminder.instruction,
                schedule=describe(schedule, tz=tz, now=now),
                due_at=reminder.next_fire_at,
            )
        )
    return firings


async def settle(
    session: AsyncSession, firings: list[Firing], *, now: datetime, tz: ZoneInfo
) -> None:
    """Move on the rows whose words were just written down.

    A repeat advances from its *scheduled* moment rather than the moment it was written, so
    a tick that ran late does not push every later fire late with it.
    """
    for firing in firings:
        reminder = await session.get(Reminder, firing.reminder_id)
        if reminder is None:
            continue
        schedule = schedule_of(reminder)
        if not schedule.repeating:
            await session.delete(reminder)
            continue
        reminder.next_fire_at = roll_forward(schedule, previous=firing.due_at, now=now, tz=tz)


async def tick(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> bool:
    """One poll. Returns whether a Cue was written."""
    moment = now or datetime.now(UTC)
    async with sessions() as session:
        if await next_cue(session) is not None:
            # Something is still waiting to be said; a second one on top of it would arrive
            # as a pile the moment the owner is free.
            return False
        reminders = await due_reminders(session, now=moment)
        if not reminders:
            return False
        firings = await prepare(session, reminders, now=moment, tz=tz)
        if not firings:
            # Every one of them was a stale repeat, and `prepare` rolled it forward.
            await session.commit()
            return False
        await add_cue(session, text=format_cue(firings, now=moment))
        await settle(session, firings, now=moment, tz=tz)
        await session.commit()
        return True


async def run_scheduler(
    sessions: async_sessionmaker[AsyncSession],
    *,
    timezone: str,
    poll_seconds: float = SCHEDULER_POLL_SECONDS,
) -> None:
    tz = ZoneInfo(timezone)
    while True:
        try:
            await tick(sessions, tz=tz)
        except asyncio.CancelledError:
            raise
        except Exception:
            # An error escaping here would silently end reminders for the rest of the process.
            logger.exception("Reminder poll failed")
        await asyncio.sleep(poll_seconds)


def format_cue(firings: list[Firing], *, now: datetime) -> str:
    """The whole batch as one request."""
    count = len(firings)
    header = "1 Reminder triggered." if count == 1 else f"{count} Reminders triggered."
    blocks = [
        header,
        (
            "If a Reminder mentions Safwa items, check their current state with query_data "
            "first: it may no longer apply. Then answer it as you would answer the user."
        ),
        "",
    ]
    for position, firing in enumerate(firings, start=1):
        blocks.append(f"{position}. Reminder #{firing.reminder_id}")
        blocks.append(f"   Text: {firing.instruction}")
        blocks.append(f"   Schedule: {firing.schedule}")
        lateness = _lateness(firing, now=now)
        if lateness:
            blocks.append(f"   {lateness}")
        blocks.append("")
    return "\n".join(blocks).strip()


def _lateness(firing: Firing, *, now: datetime) -> str:
    minutes = int((now - firing.due_at).total_seconds() // 60)
    if minutes < 15:
        return ""
    if minutes < 120:
        return f"Was due {minutes} minutes ago."
    hours = minutes // 60
    if hours < 48:
        return f"Was due {hours} hours ago."
    return f"Was due {hours // 24} days ago."
