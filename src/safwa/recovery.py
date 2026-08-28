from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .constants import SESSION_IDLE_DAYS
from .features.proposals.use_cases import cancel_batches_for_runs, expire_stale_proposals
from .models import (
    AgentRun,
    CallbackToken,
    UiSession,
)


async def recover_startup(
    session: AsyncSession, hooks: Iterable[Callable[[AsyncSession], Awaitable[None]]] = ()
) -> None:
    """Reconcile interrupted work: each feature's own hook first, then the run machinery.

    The hooks run in `MODULES` order, so Profile settles the Diary's own Reminder before
    the Reminder rebuild walks the whole table.
    """
    now = datetime.now(UTC)
    for hook in hooks:
        await hook(session)
    await session.execute(
        update(AgentRun)
        .where(AgentRun.status == "running")
        .values(status="interrupted", claimed_at=None)
    )
    await _close_abandoned_sessions(session, now=now)
    await expire_stale_proposals(session, now=now)
    await session.execute(delete(CallbackToken).where(CallbackToken.expires_at < now))
    await session.execute(delete(UiSession).where(UiSession.expires_at < now))


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
    await cancel_batches_for_runs(session, [run.id for run in abandoned])
