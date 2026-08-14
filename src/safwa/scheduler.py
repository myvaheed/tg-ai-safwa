"""The Reminder poll: find what is due, check it still matters, escalate once.

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
)
from .enums import RelevanceVerdict
from .models import Reminder, UserProfile, Workspace
from .reminders import describe, mentions_item, roll_forward, schedule_of

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
    verdict: RelevanceVerdict
    state: str | None
    revision: int


# Supplied by the runtime so this module stays free of the advisor and the bot.
Gate = Callable[[], Awaitable[bool]]
Evaluator = Callable[[Reminder], Awaitable[tuple[RelevanceVerdict, str | None]]]
Escalator = Callable[[list[Firing]], Awaitable[bool]]


async def reminders_paused(session: AsyncSession, *, now: datetime) -> bool:
    profile = await session.get(UserProfile, 1)
    if profile is None or not profile.reminders_enabled:
        return True
    snoozed = profile.reminders_snoozed_until
    return snoozed is not None and snoozed > now


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
    evaluate: Evaluator,
) -> list[Firing]:
    """Resolve each due Reminder into a Firing, rolling stale repeats forward instead.

    The two skips live here.  An instruction naming no ``#id`` has nothing to look up, and
    an unchanged ``workspace.revision`` guarantees the previous answer still holds — both
    are exact, not heuristics.
    """
    workspace = await session.get(Workspace, 1)
    revision = workspace.revision if workspace else 0
    firings: list[Firing] = []
    for reminder in reminders:
        schedule = schedule_of(reminder)
        if is_stale(reminder, now=now):
            reminder.next_fire_at = roll_forward(
                schedule, previous=reminder.next_fire_at, now=now, tz=tz
            )
            continue
        verdict, state = await _verdict(reminder, revision=revision, evaluate=evaluate)
        firings.append(
            Firing(
                reminder_id=reminder.id,
                instruction=reminder.instruction,
                schedule=describe(schedule, tz=tz, now=now),
                fire_count=reminder.fire_count,
                last_fired_at=reminder.last_fired_at,
                due_at=reminder.next_fire_at,
                verdict=verdict,
                state=state,
                revision=revision,
            )
        )
    return firings


async def _verdict(
    reminder: Reminder, *, revision: int, evaluate: Evaluator
) -> tuple[RelevanceVerdict, str | None]:
    if not mentions_item(reminder.instruction):
        return RelevanceVerdict.TRIGGER, None
    if reminder.evaluated_revision == revision and reminder.last_verdict:
        return RelevanceVerdict(reminder.last_verdict), reminder.last_state
    return await evaluate(reminder)


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
        reminder.evaluated_revision = firing.revision
        reminder.last_verdict = firing.verdict.value
        reminder.last_state = firing.state
        reminder.next_fire_at = roll_forward(schedule, previous=firing.due_at, now=now, tz=tz)


async def tick(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tz: ZoneInfo,
    gate: Gate,
    evaluate: Evaluator,
    escalate: Escalator,
    now: datetime | None = None,
) -> bool:
    """One poll. Returns whether an escalation was delivered."""
    moment = now or datetime.now(UTC)
    async with sessions() as session:
        if await reminders_paused(session, now=moment):
            return False
        reminders = await due_reminders(session, now=moment)
        if not reminders:
            return False
        if not await gate():
            # Not queued anywhere: nothing is advanced, so the rows stay due and the next
            # tick retries them once the advisor is free.
            return False
        firings = await prepare(session, reminders, now=moment, tz=tz, evaluate=evaluate)
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


async def run_scheduler(
    sessions: async_sessionmaker[AsyncSession],
    *,
    timezone: str,
    gate: Gate,
    evaluate: Evaluator,
    escalate: Escalator,
    poll_seconds: float = SCHEDULER_POLL_SECONDS,
) -> None:
    tz = ZoneInfo(timezone)
    while True:
        try:
            await tick(sessions, tz=tz, gate=gate, evaluate=evaluate, escalate=escalate)
        except asyncio.CancelledError:
            raise
        except Exception:
            # An error escaping here would silently end reminders for the rest of the process.
            logger.exception("Reminder poll failed")
        await asyncio.sleep(poll_seconds)
