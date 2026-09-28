"""Reminders' hooks: the alarm clock is a tick, and a start puts right what being down made
wrong."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from tg_agent_shell.hooks.contracts import (
    HookSpec,
    OnStarted,
    OnTick,
    Run,
    RunContext,
    Started,
    Tick,
)

from ...constants import SCHEDULER_POLL_SECONDS
from ...foundation.workspace import require_workspace
from .firing import tick
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
