from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .constants import REMINDER_CATCHUP_GRACE_MINUTES, SESSION_IDLE_DAYS
from .domain import sync_diary_reminder
from .enums import ProposalStatus
from .models import (
    AgentRun,
    AgentStep,
    CallbackToken,
    ChangeProposal,
    Reminder,
    UiSession,
    Workspace,
)
from .reminders import next_fire, on_wall_clock, roll_forward, schedule_of


async def recover_startup(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    # Before the reconcile, so a Diary Reminder created here is rolled forward with the rest.
    await sync_diary_reminder(session)
    await reconcile_reminders(session, now=now)
    await session.execute(
        update(AgentRun)
        .where(AgentRun.status == "running")
        .values(status="interrupted", claimed_at=None)
    )
    await _close_abandoned_sessions(session, now=now)
    await session.execute(
        update(ChangeProposal)
        .where(
            ChangeProposal.status == ProposalStatus.PENDING.value,
            ChangeProposal.expires_at.is_not(None),
            ChangeProposal.expires_at < now,
        )
        .values(status=ProposalStatus.STALE.value)
    )
    await session.execute(delete(CallbackToken).where(CallbackToken.expires_at < now))
    await session.execute(delete(UiSession).where(UiSession.expires_at < now))


async def reconcile_reminders(session: AsyncSession, *, now: datetime) -> None:
    """Fix `next_fire_at` on every repeating Reminder after downtime.

    Two things go stale while the process is down. A wall-clock schedule stores a local
    time, so after a timezone change "08:30" is a different UTC instant and every stored
    fire time is wrong at once; those are rebuilt. A schedule that came due meanwhile is
    rolled forward, but only once it is past the catch-up grace — inside the grace the row
    stays overdue, because the first poll firing it is the catch-up.

    A one-shot is never moved: it always fires, however late.
    """
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


async def _close_abandoned_sessions(session: AsyncSession, *, now: datetime) -> None:
    """Close sessions still waiting on a screen the owner can no longer answer.

    A session waits as long as its proposal is actionable, and a proposal expires within a
    day, so anything untouched for two is waiting on nothing.  Its open batch is cancelled
    with it, since that batch is what a stray press would otherwise still resolve into.
    """
    cutoff = now - timedelta(days=SESSION_IDLE_DAYS)
    abandoned = list(
        await session.scalars(
            select(AgentRun).where(
                AgentRun.status == "awaiting_approval",
                AgentRun.updated_at < cutoff,
            )
        )
    )
    for run in abandoned:
        run.status = "abandoned"
        run.claimed_at = None
    if not abandoned:
        return
    steps = list(
        await session.scalars(
            select(AgentStep).where(
                AgentStep.kind == "approval_batch",
                AgentStep.run_id.in_([run.id for run in abandoned]),
                AgentStep.metadata_json["status"].as_string() == "pending",
            )
        )
    )
    for step in steps:
        step.metadata_json = {**dict(step.metadata_json or {}), "status": "cancelled"}
