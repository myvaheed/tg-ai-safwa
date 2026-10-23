"""Firing Reminders: the alarm clock, and what one tick is allowed to change.

The tick *is* the alarm clock — there is no scheduling library and no in-memory timer set.
``Reminder.next_fire_at`` says **when**, and nothing else: a tick writes the words down as a
Cue and moves the Reminder on in the same transaction.  What guarantees the owner gets them
is the Cue row, which is deleted only once the turn landed — so this module holds no gate,
takes no lease and runs no Advisor turn.

One batch of Reminders waits to be said at a time: a tick that finds its own words still
waiting writes nothing, and the due Reminders it would have carried stay due for the tick
after that.  A hook's request waiting is no reason to hold them: the Cue poll says everything
waiting in one turn.

A Reminder on answers has no clock: an answer to an item its SELECT finds writes its Cue,
and only when that answer brings the count to a multiple; one set once fires at the count
itself and then deletes itself, as a one-shot does. Its words go even while others
wait, because the answer that made the count will not come again.

Do not confuse the two intervals.  The tick is ``SCHEDULER_POLL_SECONDS`` and is the
system's clock; a Reminder's own ``interval_minutes`` is a property of its row.  A deferred
Reminder waits seconds for a quiet moment, never one of its own cycles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_agent_shell.cues.queue import add_cue, words_waiting

from .api import (
    REMINDER_CATCHUP_GRACE_MINUTES,
    describe,
    roll_forward,
    schedule_of,
)
from .conditions import ConditionError, count_now, found_ids
from .model import Reminder, ScheduleKind
from .use_cases import firing_schedule

logger = logging.getLogger(__name__)

# How many due Reminders one Cue may carry.  Everything the tick found goes over
# in a single advisor turn; the rest stay overdue and the next tick takes them.
REMINDER_FIRE_BATCH = 3
# What the Cue says of a repeat's last fire, so Safwa can tell the owner it ends.
LAST_FIRE = "This is its last time."


@dataclass(frozen=True, slots=True)
class Firing:
    """One due Reminder, resolved and ready to be written into the Cue."""

    reminder_id: int
    instruction: str
    schedule: str
    due_at: datetime
    # The last fire of a repeat that ends; once written down the Reminder deletes itself.
    last: bool = False
    # How long before its moment it fires; how late it is counts from the moment.
    lead_minutes: int = 0


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
    """Resolve each due Reminder into a Firing, rolling stale repeats forward instead; one
    that rolls past its last day is deleted."""
    firings: list[Firing] = []
    for reminder in reminders:
        schedule = await firing_schedule(session, schedule_of(reminder), reminder.item_type)
        if is_stale(reminder, now=now):
            following = roll_forward(schedule, previous=reminder.next_fire_at, now=now, tz=tz)
            if following is None:
                await session.delete(reminder)
            else:
                reminder.next_fire_at = following
            continue
        firings.append(
            Firing(
                reminder_id=reminder.id,
                instruction=reminder.instruction,
                schedule=describe(schedule, tz=tz, now=now),
                due_at=reminder.next_fire_at,
                last=(schedule.total is not None and reminder.fired + 1 >= schedule.total)
                or roll_forward(schedule, previous=reminder.next_fire_at, now=now, tz=tz) is None,
                lead_minutes=schedule.lead_minutes,
            )
        )
    return firings


async def settle(
    session: AsyncSession, firings: list[Firing], *, now: datetime, tz: ZoneInfo
) -> None:
    """Move on the rows whose words were just written down, and delete a one-shot and the
    last fire of a repeat that ends.

    A repeat advances from its *scheduled* moment rather than the moment it was written, so
    a tick that ran late does not push every later fire late with it.
    """
    for firing in firings:
        reminder = await session.get(Reminder, firing.reminder_id)
        if reminder is None:
            continue
        schedule = await firing_schedule(session, schedule_of(reminder), reminder.item_type)
        if firing.last or not schedule.repeating:
            await session.delete(reminder)
            continue
        reminder.fired += 1
        reminder.next_fire_at = roll_forward(schedule, previous=firing.due_at, now=now, tz=tz)


async def tick(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> bool:
    """One tick. Returns whether a Cue was written."""
    moment = now or datetime.now(UTC)
    async with sessions() as session:
        if await words_waiting(session):
            # Reminders are still waiting to be said; a second batch on top of them would
            # arrive as a pile the moment the owner is free.
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


async def fire_conditions(
    session: AsyncSession, item_type: str, item_id: int, *, now: datetime, tz: ZoneInfo
) -> bool:
    """Write down every Reminder on answers that this answer of a Check, or Done of a Card,
    brought to a multiple of its count, or once to the count itself; a Reminder once is
    deleted as its words are written. Returns whether a Cue was written."""
    firings: list[Firing] = []
    done: list[Reminder] = []
    for reminder in await session.scalars(
        select(Reminder)
        .where(Reminder.schedule_kind == ScheduleKind.CONDITION.value)
        .order_by(Reminder.id)
    ):
        schedule = schedule_of(reminder)
        condition = schedule.condition
        if (condition.answer == "done") != (item_type == "card"):
            continue
        try:
            ids = await found_ids(session, condition)
        except ConditionError as error:
            logger.warning("Reminder #%s counts nothing: %s", reminder.id, error)
            continue
        if item_id not in ids:
            continue
        count = await count_now(session, condition, ids, item_id)
        if count is None:
            continue
        firings.append(
            Firing(
                reminder_id=reminder.id,
                instruction=reminder.instruction,
                schedule=f"{describe(schedule, tz=tz)}; {count}",
                due_at=now,
                last=condition.once,
            )
        )
        if condition.once:
            done.append(reminder)
    if not firings:
        return False
    await add_cue(session, text=format_cue(firings, now=now))
    for reminder in done:
        await session.delete(reminder)
    return True


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
        if firing.last:
            blocks.append(f"   {LAST_FIRE}")
        blocks.append("")
    return "\n".join(blocks).strip()


def _lateness(firing: Firing, *, now: datetime) -> str:
    minutes = int((now - firing.due_at).total_seconds() // 60) - firing.lead_minutes
    if minutes < 15:
        return ""
    if minutes < 120:
        return f"Was due {minutes} minutes ago."
    hours = minutes // 60
    if hours < 48:
        return f"Was due {hours} hours ago."
    return f"Was due {hours // 24} days ago."
