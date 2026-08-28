"""What a proposal does between the tool call and the write: prepared, then saved or
discarded. Plus finding the batch a screen belongs to, and moving it between row and state."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.contracts import AgentChange
from ...ai.prepare import ChangePreparer
from ...constants import PROPOSAL_EXPIRY_HOURS
from ...enums import ProposalStatus
from ...foundation.clock import utcnow
from ...foundation.errors import DomainError, StaleStateError
from ...foundation.models import Workspace
from .api import ApplyContext, ProposalRegistry
from .model import (
    ApprovalBatch,
    BatchDecision,
    BatchState,
    BatchStatus,
    ChangeProposal,
    DecideAction,
    InterruptAction,
    ProposalChange,
    QueueItem,
    RejectPendingEffect,
    ResolveCallsEffect,
)
from .reducer import reduce


async def prepare_proposal(
    session: AsyncSession,
    preparer: ChangePreparer,
    *,
    message: str,
    change: AgentChange,
) -> ChangeProposal:
    """Store one validated mutation call as its own reviewable proposal.

    Every mutation call gets its own proposal screen, so a proposal always holds exactly
    one change.  Cross-proposal references resolve by name against committed data once the
    earlier proposal has been saved.
    """
    workspace = await session.get(Workspace, 1)
    if workspace is None:
        raise DomainError("Workspace is missing")
    prepared = await preparer.prepare(session, change)
    proposal = ChangeProposal(
        message=message,
        workspace_revision=workspace.revision,
        expires_at=utcnow() + timedelta(hours=PROPOSAL_EXPIRY_HOURS),
    )
    session.add(proposal)
    await session.flush()
    session.add(
        ProposalChange(
            proposal_id=proposal.id,
            position=0,
            entity=change.entity,
            action=change.action,
            entity_id=change.id,
            expected_version=prepared.expected_version,
            values=prepared.values,
        )
    )
    return proposal


async def approve_proposal(
    session: AsyncSession,
    proposals: ProposalRegistry,
    proposal_id: int,
    *,
    allow_destructive: bool = False,
) -> list[int]:
    """Save one proposal, through the same operations the manual screens call.

    The workspace revision, the age and the ordered walk over the stored changes are the
    same whatever the proposal edits; what each change means belongs to the feature that
    owns it.
    """
    proposal = await session.get(ChangeProposal, proposal_id)
    if proposal is None or proposal.status != ProposalStatus.PENDING.value:
        raise DomainError("Proposal is no longer pending")
    if proposal.expires_at is not None and proposal.expires_at <= utcnow():
        proposal.status = ProposalStatus.STALE.value
        raise StaleStateError(
            "This proposal is more than a day old and can no longer be saved. "
            "Ask Safwa to propose it again."
        )
    workspace = await session.get(Workspace, 1)
    if workspace is None or workspace.revision != proposal.workspace_revision:
        proposal.status = ProposalStatus.STALE.value
        raise StaleStateError(
            "The board moved on after Safwa proposed this, so it was not saved. "
            "Ask Safwa to propose it again."
        )
    changes = list(
        await session.scalars(
            select(ProposalChange)
            .where(ProposalChange.proposal_id == proposal.id)
            .order_by(ProposalChange.position)
        )
    )
    context = ApplyContext(
        session=session,
        views=proposals.views,
        allow_destructive=allow_destructive,
    )
    affected: list[int] = []
    for change in changes:
        try:
            affected.extend(await proposals.handler(change.entity).apply(context, change))
        except StaleStateError:
            proposal.status = ProposalStatus.STALE.value
            raise
    proposal.status = ProposalStatus.APPROVED.value
    return affected


async def reject_proposal(session: AsyncSession, proposal_id: int) -> None:
    proposal = await session.get(ChangeProposal, proposal_id)
    if proposal and proposal.status == ProposalStatus.PENDING.value:
        proposal.status = ProposalStatus.REJECTED.value


async def mark_proposal_stale(session: AsyncSession, proposal_id: int) -> None:
    """Record that a proposal was refused, on a session that will be committed.

    `approve_proposal` marks it and raises in the same breath, so the transaction it ran in
    is rolled back and the mark with it.  The adapter that caught the refusal calls this on
    a fresh session, which is what makes the screen unanswerable rather than retryable.
    """
    proposal = await session.get(ChangeProposal, proposal_id)
    if proposal is not None:
        proposal.status = ProposalStatus.STALE.value


def state_of(batch: ApprovalBatch) -> BatchState:
    items = tuple(
        QueueItem(
            target_type=str(item.get("type")),
            target_id=int(item.get("id", 0)),
            call_ids=tuple(str(value) for value in item.get("call_ids", [])),
            decision=BatchDecision(str(item.get("status", BatchDecision.PENDING.value))),
        )
        for item in batch.queue or []
    )
    return BatchState(
        status=BatchStatus(batch.status),
        items=items,
        repair_exhausted=bool(batch.repair_exhausted),
    )


def write_state(batch: ApprovalBatch, state: BatchState) -> None:
    batch.status = state.status.value
    batch.repair_exhausted = state.repair_exhausted
    batch.queue = [
        {
            "type": item.target_type,
            "id": item.target_id,
            "call_ids": list(item.call_ids),
            "status": item.decision.value,
        }
        for item in state.items
    ]


def open_batch(
    *,
    run_id: int,
    items: Sequence[QueueItem],
    tool_calls: list[dict[str, Any]],
    repair_exhausted: bool,
) -> ApprovalBatch:
    """The batch a suspended turn opens, with its screens in the order Safwa made them.

    The caller hands over `QueueItem`s rather than rows: `write_state` is the one writer of
    the stored shape, so the queue has a single author and it is a typed one.
    """
    batch = ApprovalBatch(run_id=run_id, tool_calls=tool_calls)
    write_state(
        batch,
        BatchState(
            status=BatchStatus.PENDING,
            items=tuple(items),
            repair_exhausted=repair_exhausted,
        ),
    )
    return batch


async def number_queued_proposals(
    session: AsyncSession, items: Sequence[QueueItem], message: str
) -> None:
    """Head each proposal with its place in the queue it is waiting in.

    A proposal alone in its queue carries no heading: the number is there to say how much
    of the request is still to come.
    """
    if len(items) < 2:
        return
    for position, item in enumerate(items, start=1):
        if item.target_type != "proposal":
            continue
        proposal = await session.get(ChangeProposal, item.target_id)
        if proposal is not None:
            proposal.message = f"Proposal {position}/{len(items)}\n{message}"


async def live_batch_for_target(
    session: AsyncSession, target_type: str, target_id: int
) -> ApprovalBatch | None:
    """The suspended batch this screen belongs to, if it is still waiting on an answer."""
    batches = await session.scalars(
        select(ApprovalBatch)
        .where(ApprovalBatch.status == BatchStatus.PENDING.value)
        .order_by(ApprovalBatch.id.desc())
    )
    for batch in batches:
        if state_of(batch).item_for(target_type, target_id) is not None:
            return batch
    return None


@dataclass(frozen=True)
class BatchDecisionOutcome:
    """What one decision did to its batch, for the caller that owns the paused turn.

    `state` is what the reducer decided, handed on unflattened: `head` is the screen still
    waiting and `None` there is the batch closing.  The caller reads it rather than
    inferring it, which is why closing is decided in one place.
    """

    run_id: int
    tool_calls: list[dict[str, Any]]
    state: BatchState


async def interrupt_batch(
    session: AsyncSession, target_type: str, target_id: int, *, reason: str
) -> ApprovalBatch | None:
    """Close the batch behind this screen because the owner wrote instead of deciding it."""
    batch = await live_batch_for_target(session, target_type, target_id)
    if batch is None:
        return None
    state, effects = reduce(state_of(batch), InterruptAction(reason))
    tools = [dict(item) for item in batch.tool_calls or []]
    for effect in effects:
        match effect:
            case RejectPendingEffect():
                for item_type, item_id in effect.targets:
                    if item_type == "proposal":
                        await reject_proposal(session, item_id)
            case ResolveCallsEffect():
                for tool in tools:
                    if str(tool.get("id")) in effect.call_ids:
                        tool["status"] = "resolved"
                        tool["result"] = {
                            "status": effect.decision.value,
                            "reason": effect.reason,
                        }
    batch.tool_calls = tools
    write_state(batch, state)
    return batch


async def decide_batch_item(
    session: AsyncSession,
    proposals: ProposalRegistry,
    target_type: str,
    target_id: int,
    *,
    decision: str,
    apply_change: bool,
    render: Callable[[dict[str, Any], list[int]], dict[str, Any]],
) -> BatchDecisionOutcome | None:
    """Apply one owner decision to the batch this screen belongs to.

    ``render`` turns one resolved call into the payload the model reads back. What that
    payload says needs labels the session layer owns, so it is handed in rather than built
    here. ``None`` means there was nothing live to decide.
    """
    batch = await live_batch_for_target(session, target_type, target_id)
    if batch is None:
        return None
    # Reduce before anything is written: a press that arrives after the fact moves nothing,
    # and applying its proposal first would leave that write standing on the way out.
    state, effects = reduce(
        state_of(batch),
        DecideAction(target_type, target_id, BatchDecision(decision)),
    )
    if not effects:
        return None
    affected: list[int] = []
    if apply_change:
        if target_type != "proposal" or decision != BatchDecision.APPROVED.value:
            raise DomainError("Only an approved proposal can be applied while resolving")
        affected = await approve_proposal(session, proposals, target_id)
    if decision == BatchDecision.FAILED.value and target_type == "proposal":
        proposal = await session.get(ChangeProposal, target_id)
        if proposal is not None and proposal.status == ProposalStatus.PENDING.value:
            proposal.status = ProposalStatus.FAILED.value
    tools = [dict(item) for item in batch.tool_calls or []]
    for effect in effects:
        match effect:
            case ResolveCallsEffect():
                for tool in tools:
                    if str(tool.get("id")) in effect.call_ids:
                        tool["status"] = "resolved"
                        tool["result"] = render(tool, affected)
    batch.tool_calls = tools
    write_state(batch, state)
    if state.head is not None and state.head.target_type == "proposal":
        await refresh_queued_proposal(session, proposals, state.head.target_id)
    return BatchDecisionOutcome(run_id=batch.run_id, tool_calls=tools, state=state)


async def refresh_queued_proposal(
    session: AsyncSession, proposals: ProposalRegistry, proposal_id: int
) -> None:
    """Snapshot a proposal when it becomes visible after earlier batch decisions."""
    proposal = await session.get(ChangeProposal, proposal_id)
    workspace = await session.get(Workspace, 1)
    if proposal is None or workspace is None or proposal.status != ProposalStatus.PENDING.value:
        return
    proposal.workspace_revision = workspace.revision
    changes = list(
        await session.scalars(
            select(ProposalChange).where(ProposalChange.proposal_id == proposal_id)
        )
    )
    for change in changes:
        handler = proposals.handlers.get(change.entity)
        model = handler.version_model if handler is not None else None
        if model is None or change.entity_id is None:
            continue
        entity = await session.get(model, change.entity_id)
        change.expected_version = entity.version if entity is not None else None


async def expire_stale_proposals(session: AsyncSession, *, now: datetime) -> None:
    """Mark every pending proposal that outlived its own deadline."""
    await session.execute(
        update(ChangeProposal)
        .where(
            ChangeProposal.status == ProposalStatus.PENDING.value,
            ChangeProposal.expires_at.is_not(None),
            ChangeProposal.expires_at < now,
        )
        .values(status=ProposalStatus.STALE.value)
    )


async def cancel_batches_for_runs(session: AsyncSession, run_ids: Iterable[int]) -> None:
    """Close the open batches of sessions that are waiting on nobody.

    That batch is what a stray press would otherwise still resolve into.
    """
    ids = list(run_ids)
    if not ids:
        return
    batches = await session.scalars(
        select(ApprovalBatch).where(
            ApprovalBatch.run_id.in_(ids),
            ApprovalBatch.status == BatchStatus.PENDING.value,
        )
    )
    for batch in batches:
        batch.status = BatchStatus.CANCELLED.value
