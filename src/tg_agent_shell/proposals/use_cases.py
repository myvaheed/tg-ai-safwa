"""What a proposal does between the tool call and the write: prepared, then saved or
discarded. Plus finding the batch a screen belongs to, and moving it through its decisions:
a batch keeps its Saves and writes them together once no screen of it is left to show."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.contracts import AgentChange
from ..foundation.errors import DomainError, StaleStateError
from .api import ApplyContext, ProposalRegistry
from .model import (
    ApprovalBatch,
    BatchDecision,
    BatchEffect,
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

logger = logging.getLogger(__name__)

STALE = "The workspace moved on after this was proposed, so it was not saved. Ask for it again."
NOT_WRITTEN = (
    "Another change of this request failed, so this one was not written either. "
    "Retry it once, unchanged."
)


async def prepare_change(
    session: AsyncSession,
    preparer: ChangePreparer,
    change: AgentChange,
    *,
    earlier: Mapping[str, Any] | None = None,
) -> ProposalChange:
    """One validated mutation call, checked against live data and ready to be queued.

    Each call is prepared on its own, against committed data, even when it joins another
    call's proposal: that keeps its receipt its own. Cross-proposal references resolve by
    name against committed data once the earlier proposal has been saved.
    """
    # Preparation may settle the call in place, so what Edit prepares again is a copy of
    # the call as it was made.
    source = change.model_copy(deep=True)
    prepared = await preparer.prepare(session, change, earlier=earlier)
    return ProposalChange(
        entity=change.entity,
        action=change.action,
        entity_id=change.id,
        expected_version=prepared.expected_version,
        values=prepared.values,
        source=source,
    )


async def edit_proposal(
    session: AsyncSession,
    store: ProposalStore,
    proposals: ProposalRegistry,
    preparer: ChangePreparer,
    proposal_id: int,
    index: int,
    key: str,
    value: Any,
) -> tuple[ProposalChange, AgentChange] | None:
    """Set one field of a change, as the owner chose it (PR-EDIT-034), and return it with
    the call as preparation left it, as a call's record holds it.

    Safwa's call is prepared again with every value the owner set in place, the way Safwa's
    own is, so a refusal leaves the proposal as it was; what its last preparation read goes
    along, for what the owner left alone. Nothing is written: Save is still the owner's.
    ``None`` means the change already says exactly that.
    """
    proposal = store.proposal(proposal_id)
    if proposal is None:
        raise DomainError("Proposal is no longer pending")
    change = proposal.changes[index]
    if change.source is None:
        return None
    call, edits = proposals.edited(change.source, {**change.edits, key: value})
    if edits == change.edits:
        return None
    settled = call.model_copy(deep=True)
    fresh = await prepare_change(session, preparer, settled, earlier=change.values)
    change.action, change.entity_id = fresh.action, fresh.entity_id
    change.values, change.expected_version = fresh.values, fresh.expected_version
    change.edits = edits
    return change, settled


def queue_proposals(
    store: ProposalStore,
    changes: Sequence[tuple[str, ProposalChange]],
    *,
    workspace_revision: int,
) -> list[QueueItem]:
    """Open one response's prepared calls as proposals, in the order they were made.

    Calls in a row that change the same existing item are one proposal, so the owner reads
    one screen and presses one Save for that item. Nothing is reordered to make a group.
    """
    items: list[QueueItem] = []
    for call_id, change in changes:
        last = store.proposal(items[-1].proposal_id) if items else None
        if last is not None and _same_item(last.changes[-1], change):
            last.changes.append(change)
            items[-1] = replace(items[-1], call_ids=(*items[-1].call_ids, call_id))
            continue
        proposal = store.open_proposal(
            workspace_revision=workspace_revision, changes=[change]
        )
        items.append(QueueItem(proposal_id=proposal.id, call_ids=(call_id,)))
    number_queued_proposals(store, items)
    return items


def _same_item(earlier: ProposalChange, later: ProposalChange) -> bool:
    # A new item has no id yet, so a create never joins another change.
    return later.entity_id is not None and (earlier.entity, earlier.entity_id) == (
        later.entity,
        later.entity_id,
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
        raise StaleStateError(STALE)
    try:
        affected = await _apply(session, proposals, proposal, allow_destructive, set())
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


async def _apply(
    session: AsyncSession,
    proposals: ProposalRegistry,
    proposal: ChangeProposal,
    allow_destructive: bool,
    touched: set[tuple[str, int]],
) -> list[int]:
    """Walk one proposal's changes, in order, through the operations of their features.

    `touched` holds the items this transaction already changed: their version moved here,
    so a later change to one is handed the version it has now. The stored change keeps its
    own, for a Save tried again.
    """
    context = ApplyContext(
        session=session,
        views=proposals.views,
        allow_destructive=allow_destructive,
    )
    affected: list[int] = []
    for change in proposal.changes:
        item = (change.entity, change.entity_id) if change.entity_id is not None else None
        if item in touched:
            change = replace(
                change, expected_version=await _current_version(session, proposals, change)
            )
        affected.extend(await proposals.handler(change.entity).apply(context, change))
        if item is not None:
            touched.add(item)
    return affected


@dataclass(frozen=True)
class SaveFailure:
    """The kept Save that could not be written, so none of its queue was."""

    proposal_id: int
    error: Exception


async def write_saved(
    session: AsyncSession,
    store: ProposalStore,
    proposals: ProposalRegistry,
    items: Sequence[QueueItem],
) -> dict[int, list[int]] | SaveFailure:
    """Write the Saves a queue kept, once it has ended, all of them or none: what each
    wrote, or the one that failed.

    One transaction, committed once, so the hooks hear of every Save together. A Save is
    refused when the workspace moved on after its screen was shown.
    """
    saved = [item for item in items if item.decision is BatchDecision.APPROVED]
    written: dict[int, list[int]] = {}
    if not saved:
        return written
    current = saved[0].proposal_id
    try:
        revision = (await proposals.world(session)).revision
        touched: set[tuple[str, int]] = set()
        for item in saved:
            current = item.proposal_id
            proposal = store.proposal(current)
            if proposal is None:
                raise DomainError("Proposal is no longer pending")
            if proposal.workspace_revision != revision:
                raise StaleStateError(STALE)
            destructive = bool(item.notes.get("destructive"))
            written[current] = await _apply(session, proposals, proposal, destructive, touched)
        await session.commit()
    except Exception as error:
        await session.rollback()
        if not isinstance(error, DomainError):
            logger.exception("Saving proposal #%s failed", current)
        return SaveFailure(current, error)
    finally:
        # Ended however the write went: a review left open would hold the chat busy.
        for item in saved:
            store.end_proposal(item.proposal_id)
    return written


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


def number_queued_proposals(store: ProposalStore, items: Sequence[QueueItem]) -> None:
    """Give each proposal its place in the queue it is waiting in.

    A proposal alone in its queue has none: the number is there to say how much of the
    request is still to come.
    """
    if len(items) < 2:
        return
    for position, item in enumerate(items, start=1):
        proposal = store.proposal(item.proposal_id)
        if proposal is not None:
            proposal.place = f"{position}/{len(items)}"


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


Render = Callable[[dict[str, Any], BatchDecision, Mapping[str, Any]], dict[str, Any]]


async def interrupt_batch(
    session: AsyncSession,
    store: ProposalStore,
    proposals: ProposalRegistry,
    proposal_id: int,
    *,
    reason: str,
    render: Render,
    decision: BatchDecision = BatchDecision.DISCARDED,
) -> BatchInterruption | None:
    """Close the batch behind this screen without an answer to it, and write what it kept.

    The owner wrote instead of deciding, or nobody decided in time; `decision` is what
    the screens still waiting are recorded as. The Saves kept before are written all the same.
    """
    batch = store.batch_for_proposal(proposal_id)
    if batch is None:
        return None
    state, effects = reduce(batch.state, InterruptAction(reason, decision))
    tools = _resolved(store, batch.tool_calls, effects, render, {})
    # Closed before the write, so a press that lands while it runs finds nothing to decide.
    store.close_batch(batch)
    state, tools = await _write_kept(session, store, proposals, state, tools, render)
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
    result: Mapping[str, Any],
    render: Render,
) -> BatchDecisionOutcome | None:
    """Record one owner decision in the batch this screen belongs to.

    A Save is kept. Once no screen is left to show — the last one decided, or one
    discarded — every kept Save is written. ``render`` turns one resolved call into the
    payload the model reads back; what that payload says needs labels the session layer
    owns, so it is handed in rather than built here. ``None`` means there was nothing live
    to decide.
    """
    batch = store.batch_for_proposal(proposal_id)
    if batch is None:
        return None
    state, effects = reduce(batch.state, DecideAction(proposal_id, decision, result))
    if state is batch.state:
        return None
    if decision is BatchDecision.FAILED:
        store.end_proposal(proposal_id)
    if decision is BatchDecision.APPROVED and (kept := store.proposal(proposal_id)) is not None:
        # Off the screen now, so its time no longer runs toward the review's expiry.
        kept.shown_at = None
    tools = _resolved(store, batch.tool_calls, effects, render, result)
    batch.tool_calls, batch.state = tools, state
    if state.head is not None:
        await refresh_queued_proposal(session, store, proposals, state.head.proposal_id)
    else:
        # Closed before the write, so a press that lands while it runs finds nothing to decide.
        store.close_batch(batch)
        state, tools = await _write_kept(session, store, proposals, state, tools, render)
        batch.tool_calls, batch.state = tools, state
    return BatchDecisionOutcome(
        run_id=batch.run_id,
        tool_calls=tools,
        state=state,
        interaction_token=batch.interaction_token,
    )


def _resolved(
    store: ProposalStore,
    tool_calls: Sequence[dict[str, Any]],
    effects: Sequence[BatchEffect],
    render: Render,
    result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """The batch's calls with the results these effects give them, and the screens they
    take away ended. A call closed with a reason never reached a decision of its own."""
    tools = [dict(item) for item in tool_calls]
    for effect in effects:
        match effect:
            case RejectPendingEffect():
                for rejected_id in effect.proposal_ids:
                    store.end_proposal(rejected_id)
            case ResolveCallsEffect():
                for tool in tools:
                    if str(tool.get("id")) not in effect.call_ids:
                        continue
                    tool["result"] = (
                        {"status": effect.decision.value, "reason": effect.reason}
                        if effect.reason is not None
                        else render(tool, effect.decision, result)
                    )
    return tools


async def _write_kept(
    session: AsyncSession,
    store: ProposalStore,
    proposals: ProposalRegistry,
    state: BatchState,
    tools: list[dict[str, Any]],
    render: Render,
) -> tuple[BatchState, list[dict[str, Any]]]:
    """Write the batch's kept Saves and hand each of their calls what came of it."""
    outcome = await write_saved(session, store, proposals, state.items)
    items: list[QueueItem] = []
    for item in state.items:
        if item.decision is BatchDecision.APPROVED:
            for tool in tools:
                if str(tool.get("id")) not in item.call_ids:
                    continue
                if not isinstance(outcome, SaveFailure):
                    payload = {**item.notes, "affected_ids": outcome[item.proposal_id]}
                    tool["result"] = render(tool, BatchDecision.APPROVED, payload)
                elif outcome.proposal_id == item.proposal_id:
                    tool["result"] = render(
                        tool, BatchDecision.FAILED, {"error": str(outcome.error)}
                    )
                else:
                    tool["result"] = {"status": BatchDecision.FAILED.value, "reason": NOT_WRITTEN}
            if isinstance(outcome, SaveFailure):
                item = replace(item, decision=BatchDecision.FAILED)
        items.append(item)
    return replace(state, items=tuple(items)), tools


async def refresh_queued_proposal(
    session: AsyncSession,
    store: ProposalStore,
    proposals: ProposalRegistry,
    proposal_id: int,
) -> None:
    """Snapshot a proposal as its screen is about to be shown: what its Save is checked
    against when the queue is written."""
    proposal = store.proposal(proposal_id)
    if proposal is None:
        return
    proposal.workspace_revision = (await proposals.world(session)).revision
    for change in proposal.changes:
        change.expected_version = await _current_version(session, proposals, change)


async def _current_version(
    session: AsyncSession, proposals: ProposalRegistry, change: ProposalChange
) -> int | None:
    """The version the change's item has now, or the recorded one where none is kept."""
    handler = proposals.handlers.get(change.entity)
    model = handler.version_model if handler is not None else None
    if model is None or change.entity_id is None:
        return change.expected_version
    entity = await session.get(model, change.entity_id)
    return entity.version if entity is not None else None
