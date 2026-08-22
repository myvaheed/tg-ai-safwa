"""The time-driven polls: due Reminders, and a Sprint that has outlived its end date.

The poll *is* the alarm clock — there is no scheduling library and no in-memory timer set.
``Reminder.next_fire_at`` is the only column the loop reads, and it is advanced **only after
an escalation succeeds**.  That single ordering rule is what makes a cancelled or crashed
turn lose nothing: the row is still overdue, so the next tick finds it again and retries.

Do not confuse the two intervals.  The poll is ``SCHEDULER_POLL_SECONDS`` and is the
system's clock; a Reminder's own ``interval_minutes`` is a property of its row.  A deferred
Reminder waits seconds for a quiet moment, never one of its own cycles.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .constants import (
    REMINDER_CATCHUP_GRACE_MINUTES,
    REMINDER_FIRE_BATCH,
    SCHEDULER_POLL_SECONDS,
    SPRINT_EXPIRY_POLL_SECONDS,
)
from .domain import expire_due_sprint
from .features.reminders.schedule import describe, roll_forward, schedule_of
from .models import Reminder

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Firing:
    """One due Reminder, resolved and ready to be written into an escalation."""

    reminder_id: int
    instruction: str
    schedule: str
    fire_count: int
    last_fired_at: datetime | None
    due_at: datetime


# Supplied by the runtime so this module stays free of the advisor and the bot.
Gate = Callable[[], Awaitable[bool]]
Escalator = Callable[[list[Firing]], Awaitable[bool]]
LeaseCheck = Callable[[], bool]
LeaseRelease = Callable[[], None]
Announcer = Callable[[int], Awaitable[None]]


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

    A weekend offline must not produce 32 posture escalations, so anything past the grace
    window rolls forward silently.  A one-shot is never stale: it always fires, however
    late, and the escalation says how late.
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
                fire_count=reminder.fire_count,
                last_fired_at=reminder.last_fired_at,
                due_at=reminder.next_fire_at,
            )
        )
    return firings


async def settle(
    session: AsyncSession, firings: list[Firing], *, now: datetime, tz: ZoneInfo
) -> None:
    """Advance the rows an escalation actually delivered. Never called before it succeeds.

    A repeat advances from its *scheduled* moment rather than the delivery moment, so a
    turn that took four minutes does not push every later fire four minutes out.
    """
    for firing in firings:
        reminder = await session.get(Reminder, firing.reminder_id)
        if reminder is None:
            continue
        schedule = schedule_of(reminder)
        if not schedule.repeating:
            await session.delete(reminder)
            continue
        reminder.last_fired_at = now
        reminder.fire_count += 1
        reminder.next_fire_at = roll_forward(schedule, previous=firing.due_at, now=now, tz=tz)


async def tick(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tz: ZoneInfo,
    gate: Gate,
    escalate: Escalator,
    still_current: LeaseCheck = lambda: True,
    release: LeaseRelease = lambda: None,
    now: datetime | None = None,
) -> bool:
    """One poll. Returns whether an escalation was delivered."""
    moment = now or datetime.now(UTC)
    async with sessions() as session:
        reminders = await due_reminders(session, now=moment)
        if not reminders:
            return False
        reminder_ids = [reminder.id for reminder in reminders]
    if not await gate():
        # Not queued anywhere: nothing is advanced, so the rows stay due and the next
        # tick retries them once the advisor is free.
        return False
    try:
        async with sessions() as session:
            attached = list(
                await session.scalars(select(Reminder).where(Reminder.id.in_(reminder_ids)))
            )
            by_id = {reminder.id: reminder for reminder in attached}
            reminders = [by_id[item_id] for item_id in reminder_ids if item_id in by_id]
            firings = await prepare(session, reminders, now=moment, tz=tz)
            if not still_current():
                return False
            await session.commit()
            if not firings:
                return False

        delivered = await escalate(firings)
        if not delivered:
            return False
        async with sessions() as session:
            await settle(session, firings, now=moment, tz=tz)
            await session.commit()
        return True
    finally:
        release()


async def run_sprint_expiry(
    sessions: async_sessionmaker[AsyncSession],
    *,
    announce: Announcer,
    poll_seconds: float = SPRINT_EXPIRY_POLL_SECONDS,
) -> None:
    """Close a Sprint that ran past its planned end date and say so once.

    A separate poll from the Reminder one: it takes no lease and asks the advisor nothing,
    because closing a Sprint at midnight is arithmetic, not a conversation.
    """
    while True:
        try:
            async with sessions() as session:
                sprint = await expire_due_sprint(session)
                number = sprint.number if sprint is not None else None
                await session.commit()
            if number is not None:
                await announce(number)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sprint expiry poll failed")
        await asyncio.sleep(poll_seconds)


async def run_scheduler(
    sessions: async_sessionmaker[AsyncSession],
    *,
    timezone: str,
    gate: Gate,
    escalate: Escalator,
    still_current: LeaseCheck = lambda: True,
    release: LeaseRelease = lambda: None,
    poll_seconds: float = SCHEDULER_POLL_SECONDS,
) -> None:
    tz = ZoneInfo(timezone)
    while True:
        try:
            await tick(
                sessions,
                tz=tz,
                gate=gate,
                still_current=still_current,
                release=release,
                escalate=escalate,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # An error escaping here would silently end reminders for the rest of the process.
            logger.exception("Reminder poll failed")
        await asyncio.sleep(poll_seconds)
