"""How a batch of review screens moves, and nothing else.

`reduce` reads no row and writes none: given where the batch stands and what happened, it
says where it now stands and what the caller has to do about it. Applying a proposal,
rendering the next screen and resuming the session stay outside, which is what makes every
transition testable as a table.
"""

from __future__ import annotations

from dataclasses import replace

from .model import (
    BatchAction,
    BatchDecision,
    BatchEffect,
    BatchState,
    DecideAction,
    InterruptAction,
    QueueItem,
    RejectPendingEffect,
    ResolveCallsEffect,
)

INTERRUPTED = "The user continued with a new message."
EXPIRED = "Nobody answered the review in time, so the request was closed."
NOT_SHOWN = (
    "The user discarded an earlier proposal of this request, so this one was not shown. "
    "Tell the user it was not shown; propose it again only if they ask."
)


def reduce(state: BatchState, action: BatchAction) -> tuple[BatchState, tuple[BatchEffect, ...]]:
    match action:
        case DecideAction():
            return _decide(state, action)
        case InterruptAction():
            return _interrupt(state, action)


def _decide(
    state: BatchState, action: DecideAction
) -> tuple[BatchState, tuple[BatchEffect, ...]]:
    item = state.item_for(action.proposal_id)
    # A screen already answered, and a batch whose screens all are, both mean the press
    # arrived after the fact: the state comes back as it was, with nothing to do.
    if item is None or item.decision is not BatchDecision.PENDING:
        return state, ()
    decided = replace(item, decision=action.decision, notes=action.notes)
    if action.decision is BatchDecision.APPROVED:
        # A Save is kept, not written: its call learns what it wrote once the queue ends.
        return _replaced(state, {item.proposal_id: decided}), ()
    effects: list[BatchEffect] = [ResolveCallsEffect(item.call_ids, action.decision)]
    replaced = {item.proposal_id: decided}
    if action.decision is BatchDecision.DISCARDED:
        # A Discard ends the queue: the screens behind it are never shown.
        rest = tuple(
            candidate
            for candidate in state.items
            if candidate is not item and candidate.decision is BatchDecision.PENDING
        )
        if rest:
            effects.append(RejectPendingEffect(tuple(other.proposal_id for other in rest)))
            effects.append(
                ResolveCallsEffect(
                    tuple(call_id for other in rest for call_id in other.call_ids),
                    BatchDecision.DISCARDED,
                    NOT_SHOWN,
                )
            )
            for other in rest:
                replaced[other.proposal_id] = replace(other, decision=BatchDecision.DISCARDED)
    return _replaced(state, replaced), tuple(effects)


def _replaced(state: BatchState, items: dict[int, QueueItem]) -> BatchState:
    return replace(
        state,
        items=tuple(items.get(candidate.proposal_id, candidate) for candidate in state.items),
    )


def _interrupt(
    state: BatchState, action: InterruptAction
) -> tuple[BatchState, tuple[BatchEffect, ...]]:
    pending = tuple(item for item in state.items if item.decision is BatchDecision.PENDING)
    items = tuple(
        replace(item, decision=action.decision) if item in pending else item
        for item in state.items
    )
    effects: list[BatchEffect] = []
    if pending:
        effects.append(RejectPendingEffect(tuple(item.proposal_id for item in pending)))
        effects.append(
            ResolveCallsEffect(
                tuple(call_id for item in pending for call_id in item.call_ids),
                action.decision,
                action.reason,
            )
        )
    return replace(state, items=items), tuple(effects)
