"""Reminders' hooks: the alarm clock is a tick, an answer fires a condition, and a start puts
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
from ..cards.use_cases import CARD_DONE
from ..checks.use_cases import CHECK_ANSWERED
from .firing import fire_conditions, tick
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


async def answered(event: Committed) -> tuple[tuple[str, int], ...]:
    """A new answer; a corrected one is not a new answer, so it fires nothing."""
    if (event.data or {}).get("corrected"):
        return ()
    return (("card" if event.kind == CARD_DONE else "check", event.subject_id),)


async def fire_on_answer(item: tuple[str, int], context: RunContext) -> None:
    item_type, item_id = item
    async with context.sessions() as session:
        tz = ZoneInfo((await require_workspace(session)).timezone)
        if await fire_conditions(session, item_type, item_id, now=datetime.now(UTC), tz=tz):
            await session.commit()


REMINDER_CONDITION_HOOK = HookSpec(
    name="reminders.condition",
    owner="reminders",
    on=(OnCommitted(kind=CHECK_ANSWERED), OnCommitted(kind=CARD_DONE)),
    evaluate=answered,
    effect=Run(fire_on_answer),
    title="Reminders on answers",
    description="Writes down the Reminders an answer just made due, for Safwa to say.",
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
