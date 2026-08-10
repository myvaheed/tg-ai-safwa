from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import ProposalStatus
from .models import (
    AgentRun,
    AgentStep,
    CallbackToken,
    ChangeProposal,
    ScheduledJob,
    UiSession,
)


async def recover_startup(session: AsyncSession) -> None:
    now = datetime.now(UTC)
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
    await session.execute(
        update(ScheduledJob).where(ScheduledJob.status == "running").values(status="pending")
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
