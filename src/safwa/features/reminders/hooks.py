"""Reminders' hooks: the alarm clock is a tick, a Check's answer fires a run, and a start puts
right what being down made wrong."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import (
    HookSpec,
    OnCommitted,
    OnStarted,
    OnTick,
    Run,
    RunContext,
    Started,
    Tick,
)

from ...constants import SCHEDULER_POLL_SECONDS
from ...foundation.workspace import require_workspace
from ..checks.use_cases import CHECK_ANSWERED
from .firing import fire_runs, tick
from .use_cases import reconcile_reminders


async def every_look(event: Tick) -> tuple[Tick, ...]:
    return (event,)


async def fire_due(event: Tick, context: RunContext) -> None:
    async with context.sessions() as session:
        tz = ZoneInfo((await require_workspace(session)).timezone)
    await tick(context.sessions, tz=tz)


REMINDER_FIRE_HOOK = HookSpec(
    name="reminders.fire",
    owner="reminders",
    on=(OnTick(every=timedelta(seconds=SCHEDULER_POLL_SECONDS)),),
    evaluate=every_look,
    effect=Run(fire_due),
    title="Reminders",
    description="Writes down the Reminders that came due, for Safwa to say.",
)


async def answered_check(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def fire_check_runs(check_id: int, context: RunContext) -> None:
    async with context.sessions() as session:
        tz = ZoneInfo((await require_workspace(session)).timezone)
        if await fire_runs(session, check_id, now=datetime.now(UTC), tz=tz):
            await session.commit()


REMINDER_RUN_HOOK = HookSpec(
    name="reminders.check_run",
    owner="reminders",
    on=(OnCommitted(kind=CHECK_ANSWERED),),
    evaluate=answered_check,
    effect=Run(fire_check_runs),
    title="Reminders on a Check",
    description="Writes down the Reminders a Check's answers just made due, for Safwa to say.",
)


async def at_start(event: Started) -> tuple[Started, ...]:
    return (event,)


async def reconcile(event: Started, context: RunContext) -> None:
    async with context.sessions() as session:
        await reconcile_reminders(session)
        await session.commit()


REMINDER_START_HOOK = HookSpec(
    name="reminders.start",
    owner="reminders",
    on=(OnStarted(),),
    evaluate=at_start,
    effect=Run(reconcile),
    title="Reminders after a restart",
    description="At the start, works out again the Reminders that being down made wrong.",
)
