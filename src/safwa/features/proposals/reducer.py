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
    RejectPendingEffect,
    ResolveCallsEffect,
)

INTERRUPTED = "The user continued with a new message."


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
    # arrived after the fact: nothing left to decide and nothing to tell the model.
    if item is None or item.decision is not BatchDecision.PENDING:
        return state, ()
    decided = replace(
        state,
        items=tuple(
            replace(candidate, decision=action.decision) if candidate is item else candidate
            for candidate in state.items
        ),
    )
    return decided, (ResolveCallsEffect(item.call_ids, action.decision),)


def _interrupt(
    state: BatchState, action: InterruptAction
) -> tuple[BatchState, tuple[BatchEffect, ...]]:
    pending = tuple(item for item in state.items if item.decision is BatchDecision.PENDING)
    items = tuple(
        replace(item, decision=BatchDecision.DISCARDED) if item in pending else item
        for item in state.items
    )
    effects: list[BatchEffect] = []
    if pending:
        effects.append(RejectPendingEffect(tuple(item.proposal_id for item in pending)))
        effects.append(
            ResolveCallsEffect(
                tuple(call_id for item in pending for call_id in item.call_ids),
                BatchDecision.DISCARDED,
                action.reason,
            )
        )
    return replace(state, items=items), tuple(effects)
