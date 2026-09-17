"""The Sprint's own clockwork: the midnight that ends it, and the summary said once it has."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import (
    Advise,
    HookSpec,
    OnCommitted,
    OnTick,
    Run,
    RunContext,
    Tick,
)

from .api import SPRINT_ENDED
from .model import Sprint
from .use_cases import expire_due_sprint, sprint_summary


async def midnight(session: AsyncSession) -> time:
    """The local midnight: a Sprint runs through its last day and ends when that day does."""
    return time(0, 0)


async def passed_midnight(event: Tick) -> tuple[str, ...]:
    return (event.at,)


async def expire_due_sprint_now(session: AsyncSession) -> None:
    """Close the running Sprint if the midnight after its last day has passed."""
    await expire_due_sprint(session)


async def expire_sprint(marker: str, context: RunContext) -> None:
    async with context.sessions() as session:
        await expire_due_sprint_now(session)
        await session.commit()


SPRINT_EXPIRY_HOOK = HookSpec(
    name="planning.sprint_expiry",
    owner="planning",
    on=(OnTick(at=midnight),),
    evaluate=passed_midnight,
    effect=Run(expire_sprint),
    title="Sprint expiry",
    description="At the midnight after a Sprint's last day, ends it.",
)


async def ended_sprint(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def sprint_summary_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The summary of each Sprint that ended, written from its record as it is about to be said."""
    summaries = []
    for sprint_id in items:
        sprint = await session.get(Sprint, sprint_id)
        if sprint is not None:
            summaries.append(await sprint_summary(session, sprint))
    return "\n\n".join(summaries) if summaries else None


SPRINT_SUMMARY_HOOK = HookSpec(
    name="planning.sprint_summary",
    owner="planning",
    on=(OnCommitted(kind=SPRINT_ENDED),),
    evaluate=ended_sprint,
    effect=Advise(prepare=sprint_summary_request),
    title="Sprint summary",
    description="When a Sprint ends, tells you how it went and asks about what is still open.",
)
