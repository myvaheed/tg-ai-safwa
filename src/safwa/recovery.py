from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .constants import REMINDER_CATCHUP_GRACE_MINUTES
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
    await reconcile_reminders(session, now=now)
    await session.execute(
        update(AgentRun).where(AgentRun.status == "running").values(status="interrupted")
    )
    await _close_interrupted_approval_batches(session)
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
    """Bring Reminder schedules back in line with the wall clock after downtime.

    There is nothing to register: the rows *are* the schedule, so boot only reconciles.
    Anything still inside the catch-up grace is deliberately left overdue — the first poll
    firing it *is* the catch-up.

    A timezone change is handled here rather than at the change site because a wall-clock
    schedule stores a local time: "08:30" means a different UTC instant after the move, and
    every stored `next_fire_at` is stale at once.
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


async def _close_interrupted_approval_batches(session: AsyncSession) -> None:
    """Release approval batches whose model continuation never returned.

    ``resolve_approval`` marks a batch ``resuming`` before calling the provider.  A crash in
    between leaves it claiming the proposal forever, so every later Save or Discard is routed
    into a continuation that cannot finish.  The queue was already fully resolved at that
    point, so the batch is simply closed and the owner continues with a new message.
    """
    steps = list(
        await session.scalars(
            select(AgentStep).where(
                AgentStep.kind == "approval_batch",
                AgentStep.metadata_json["status"].as_string() == "resuming",
            )
        )
    )
    for step in steps:
        metadata = dict(step.metadata_json or {})
        metadata["status"] = "completed"
        metadata["continuation_error"] = "InterruptedAtStartup"
        step.metadata_json = metadata
