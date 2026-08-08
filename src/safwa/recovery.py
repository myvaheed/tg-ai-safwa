from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import DraftStatus, ProposalStatus
from .models import (
    AgentRun,
    CallbackToken,
    CardDraft,
    CardDraftBundle,
    ChangeProposal,
    ScheduledJob,
    UiSession,
)


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
    expired_bundles = list(
        await session.scalars(
            select(CardDraftBundle).where(
                CardDraftBundle.status.in_(
                    [DraftStatus.EDITING.value, DraftStatus.READY.value, DraftStatus.REVIEWED.value]
                ),
                CardDraftBundle.expires_at.is_not(None),
                CardDraftBundle.expires_at < now,
            )
        )
    )
    for bundle in expired_bundles:
        bundle.status = DraftStatus.EXPIRED.value
        for draft in await session.scalars(
            select(CardDraft).where(CardDraft.bundle_id == bundle.id)
        ):
            if draft.status not in {DraftStatus.COMMITTED.value, DraftStatus.DISCARDED.value}:
                draft.status = DraftStatus.EXPIRED.value
    await session.execute(
        update(ScheduledJob).where(ScheduledJob.status == "running").values(status="pending")
    )
