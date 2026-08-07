from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import ProposalStatus
from .models import AgentRun, CallbackToken, ChangeProposal, ScheduledJob, UiSession


async def recover_startup(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(AgentRun).where(AgentRun.status == "running").values(status="interrupted")
    )
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
    await session.execute(
        update(ScheduledJob).where(ScheduledJob.status == "running").values(status="pending")
    )
