from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime import RunStatus

from .enums import MessageKind
from .models import (
    AgentRun,
    CallbackToken,
    TelegramMessage,
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
    # Both end the same way, so both are recorded the same way.  `interrupted` is the one
    # a later turn may pick up, and nothing after a restart can be its caller.
    await session.execute(
        update(AgentRun)
        .where(
            AgentRun.status.in_(
                (RunStatus.RUNNING.value, RunStatus.AWAITING_APPROVAL.value)
            )
        )
        .values(status=RunStatus.ABANDONED.value, claimed_at=None)
    )
    # Proposal reviews live in the running process, so a restart has already ended every
    # one of them.  What is left in the database is what pointed at them: a button that
    # would still claim, and a screen whose `related_id` would name a later review.
    await session.execute(
        update(TelegramMessage)
        .where(TelegramMessage.kind == MessageKind.APPROVAL.value)
        .values(related_id=None)
        .execution_options(synchronize_session=False)
    )
    # A screen is a view of state a running Safwa was holding, so a restart makes every
    # one of them out of date and every button on them unanswerable.
    await session.execute(delete(CallbackToken).execution_options(synchronize_session=False))
    await session.execute(
        delete(UiSession)
        .where(UiSession.expires_at < now)
        .execution_options(synchronize_session=False)
    )
