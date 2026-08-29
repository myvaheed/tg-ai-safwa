from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import MessageKind
from .models import (
    AgentRun,
    AgentRunStatus,
    CallbackToken,
    TelegramMessage,
    UiSession,
)
from .telegram.callbacks import PROPOSAL_CALLBACK_ACTIONS


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
        .where(AgentRun.status == AgentRunStatus.RUNNING.value)
        .values(status=AgentRunStatus.INTERRUPTED.value, claimed_at=None)
    )
    await session.execute(
        update(AgentRun)
        .where(AgentRun.status == AgentRunStatus.AWAITING_APPROVAL.value)
        .values(status=AgentRunStatus.ABANDONED.value, claimed_at=None)
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
    await session.execute(
        delete(CallbackToken)
        .where(
            or_(
                CallbackToken.expires_at < now,
                CallbackToken.action.in_(PROPOSAL_CALLBACK_ACTIONS),
            )
        )
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(UiSession)
        .where(UiSession.expires_at < now)
        .execution_options(synchronize_session=False)
    )
