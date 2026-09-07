"""What a proposal does between the tool call and the write: prepared, then saved or
discarded. Plus finding the batch a screen belongs to, and moving it through its decisions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.contracts import AgentChange
from ..foundation.errors import DomainError, StaleStateError
from .api import ApplyContext, ProposalRegistry
from .model import (
    ApprovalBatch,
    BatchDecision,
    BatchState,
    ChangeProposal,
    DecideAction,
    InterruptAction,
    ProposalChange,
    QueueItem,
    RejectPendingEffect,
    ResolveCallsEffect,
)
from .prepare import ChangePreparer
from .reducer import reduce
from .store import ProposalStore


async def prepare_proposal(
    session: AsyncSession,
    store: ProposalStore,
    preparer: ChangePreparer,
    *,
    message: str,
    change: AgentChange,
) -> ChangeProposal:
    """Open one validated mutation call as its own reviewable proposal.

    Every mutation call gets its own proposal screen, so a proposal always holds exactly
    one change.  Cross-proposal references resolve by name against committed data once the
    earlier proposal has been saved.
    """
    world = await preparer.world(session)
    prepared = await preparer.prepare(session, change)
    return store.open_proposal(
        message=message,
        workspace_revision=world.revision,
        changes=[
            ProposalChange(
                entity=change.entity,
                action=change.action,
                entity_id=change.id,
                expected_version=prepared.expected_version,
                values=prepared.values,
            )
        ],
    )


async def approve_proposal(
    session: AsyncSession,
    store: ProposalStore,
    proposals: ProposalRegistry,
    proposal_id: int,
    *,
    allow_destructive: bool = False,
) -> list[int]:
    """Save one proposal, through the same operations the manual screens call.

    The workspace revision and the ordered walk over its changes are the same whatever the
    proposal edits; what each change means belongs to the feature that owns it.

    This is where the operation ends, for the manual Save and for autoapproval alike: the
    write is committed here and the review is taken off the screen only once that commit
    stands, so a failure leaves a screen the owner can still answer rather than a change
    that was neither written nor refused.  A refusal is the exception, and a deliberate
    one: it ends the review because it makes the screen unanswerable rather than retryable.
    """
    proposal = store.proposal(proposal_id)
    if proposal is None:
        raise DomainError("Proposal is no longer pending")
    world = await proposals.world(session)
    if world.revision != proposal.workspace_revision:
        store.end_proposal(proposal_id)
        raise StaleStateError(
            "The workspace moved on after this was proposed, so it was not saved. "
            "Ask for it again."
        )
    context = ApplyContext(
        session=session,
        views=proposals.views,
        allow_destructive=allow_destructive,
    )
    affected: list[int] = []
    try:
        for change in proposal.changes:
            affected.extend(await proposals.handler(change.entity).apply(context, change))
        await session.commit()
    except StaleStateError:
        await session.rollback()
        store.end_proposal(proposal_id)
        raise
    except Exception:
        await session.rollback()
        raise
    store.end_proposal(proposal_id)
    return affected


def open_batch(
    *,
    run_id: int,
    items: Sequence[QueueItem],
    tool_calls: list[dict[str, Any]],
    repair_exhausted: bool,
    request: str = "",
) -> ApprovalBatch:
    """The batch a suspended turn opens, with its screens in the order they were made."""
    return ApprovalBatch(
        run_id=run_id,
        tool_calls=tool_calls,
        state=BatchState(items=tuple(items), repair_exhausted=repair_exhausted),
        request=request,
    )


def number_queued_proposals(
    store: ProposalStore, items: Sequence[QueueItem], message: str
) -> None:
    """Head each proposal with its place in the queue it is waiting in.

    A proposal alone in its queue carries no heading: the number is there to say how much
    of the request is still to come.
    """
    if len(items) < 2:
        return
    for position, item in enumerate(items, start=1):
        proposal = store.proposal(item.proposal_id)
        if proposal is not None:
            proposal.message = f"Proposal {position}/{len(items)}\n{message}"


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
    interaction_token: str = ""


@dataclass(frozen=True)
class BatchInterruption:
    """The turn that lost its screens, and the calls it now has to answer for."""

    run_id: int
    tool_calls: list[dict[str, Any]]
    interaction_token: str = ""


def interrupt_batch(
    store: ProposalStore, proposal_id: int, *, reason: str
) -> BatchInterruption | None:
    """Close the batch behind this screen because the owner wrote instead of deciding it."""
    batch = store.batch_for_proposal(proposal_id)
    if batch is None:
        return None
    _state, effects = reduce(batch.state, InterruptAction(reason))
    tools = [dict(item) for item in batch.tool_calls]
    for effect in effects:
        match effect:
            case RejectPendingEffect():
                for rejected_id in effect.proposal_ids:
                    store.end_proposal(rejected_id)
            case ResolveCallsEffect():
                for tool in tools:
                    if str(tool.get("id")) in effect.call_ids:
                        tool["result"] = {
                            "status": effect.decision.value,
                            "reason": effect.reason,
                        }
    store.close_batch(batch)
    return BatchInterruption(
        run_id=batch.run_id, tool_calls=tools, interaction_token=batch.interaction_token
    )


async def decide_batch_item(
    session: AsyncSession,
    store: ProposalStore,
    proposals: ProposalRegistry,
    proposal_id: int,
    *,
    decision: BatchDecision,
    apply_change: bool,
    render: Callable[[dict[str, Any], list[int]], dict[str, Any]],
) -> BatchDecisionOutcome | None:
    """Apply one owner decision to the batch this screen belongs to.

    ``render`` turns one resolved call into the payload the model reads back. What that
    payload says needs labels the session layer owns, so it is handed in rather than built
    here. ``None`` means there was nothing live to decide.
    """
    batch = store.batch_for_proposal(proposal_id)
    if batch is None:
        return None
    # Reduce before anything is written: a press that arrives after the fact moves nothing,
    # and applying its proposal first would leave that write standing on the way out.
    state, effects = reduce(batch.state, DecideAction(proposal_id, decision))
    if not effects:
        return None
    affected: list[int] = []
    if apply_change:
        if decision is not BatchDecision.APPROVED:
            raise DomainError("Only an approved proposal can be applied while resolving")
        # The write and its commit come first, so everything below moves the queue on
        # against a database that already holds what this decision decided.
        affected = await approve_proposal(session, store, proposals, proposal_id)
    if decision is BatchDecision.FAILED:
        store.end_proposal(proposal_id)
    tools = [dict(item) for item in batch.tool_calls]
    for effect in effects:
        match effect:
            case ResolveCallsEffect():
                for tool in tools:
                    if str(tool.get("id")) in effect.call_ids:
                        tool["result"] = render(tool, affected)
    batch.tool_calls = tools
    batch.state = state
    if state.head is not None:
        await refresh_queued_proposal(session, store, proposals, state.head.proposal_id)
    if state.head is None:
        # The batch has done its whole job, and a stray press has nothing left to reach.
        store.close_batch(batch)
    return BatchDecisionOutcome(
        run_id=batch.run_id,
        tool_calls=tools,
        state=state,
        interaction_token=batch.interaction_token,
    )


async def refresh_queued_proposal(
    session: AsyncSession,
    store: ProposalStore,
    proposals: ProposalRegistry,
    proposal_id: int,
) -> None:
    """Snapshot a proposal when it becomes visible after earlier batch decisions."""
    proposal = store.proposal(proposal_id)
    if proposal is None:
        return
    proposal.workspace_revision = (await proposals.world(session)).revision
    for change in proposal.changes:
        handler = proposals.handlers.get(change.entity)
        model = handler.version_model if handler is not None else None
        if model is None or change.entity_id is None:
            continue
        entity = await session.get(model, change.entity_id)
        change.expected_version = entity.version if entity is not None else None
