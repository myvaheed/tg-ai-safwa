"""Every transition of a batch of review screens, as a table.

The reducer is where "what happens when the owner answers a screen" is decided, so each
row here is one press and what it leaves behind. Applying a proposal and resuming the
session are the caller's, and none of them is needed to check a transition.
"""

from __future__ import annotations

import pytest

from safwa.features.proposals.model import (
    BatchDecision,
    BatchState,
    BatchStatus,
    CloseBatchEffect,
    DecideAction,
    InterruptAction,
    QueueItem,
    RejectPendingEffect,
    ResolveCallsEffect,
    ShowNextEffect,
)
from safwa.features.proposals.reducer import INTERRUPTED, reduce

PENDING = BatchDecision.PENDING
APPROVED = BatchDecision.APPROVED
DISCARDED = BatchDecision.DISCARDED
FAILED = BatchDecision.FAILED


def item(target_id: int, decision: BatchDecision = PENDING) -> QueueItem:
    return QueueItem(
        target_type="proposal",
        target_id=target_id,
        call_ids=(f"call-{target_id}",),
        decision=decision,
    )


def batch(*items: QueueItem, status: BatchStatus = BatchStatus.PENDING, exhausted: bool = False):
    return BatchState(status=status, items=items, repair_exhausted=exhausted)


@pytest.mark.parametrize(
    ("state", "action", "expected_status", "expected_decisions", "expected_effects"),
    [
        (
            batch(item(1)),
            DecideAction("proposal", 1, APPROVED),
            BatchStatus.COMPLETED,
            [APPROVED],
            (ResolveCallsEffect(("call-1",), APPROVED), CloseBatchEffect(False)),
        ),
        (
            batch(item(1), item(2)),
            DecideAction("proposal", 1, APPROVED),
            BatchStatus.PENDING,
            [APPROVED, PENDING],
            (ResolveCallsEffect(("call-1",), APPROVED), ShowNextEffect("proposal", 2)),
        ),
        (
            batch(item(1), item(2)),
            DecideAction("proposal", 1, DISCARDED),
            BatchStatus.PENDING,
            [DISCARDED, PENDING],
            (ResolveCallsEffect(("call-1",), DISCARDED), ShowNextEffect("proposal", 2)),
        ),
        (
            batch(item(1, APPROVED), item(2)),
            DecideAction("proposal", 2, FAILED),
            BatchStatus.COMPLETED,
            [APPROVED, FAILED],
            (ResolveCallsEffect(("call-2",), FAILED), CloseBatchEffect(False)),
        ),
        (
            batch(item(1, APPROVED), item(2), exhausted=True),
            DecideAction("proposal", 2, APPROVED),
            BatchStatus.COMPLETED,
            [APPROVED, APPROVED],
            (ResolveCallsEffect(("call-2",), APPROVED), CloseBatchEffect(True)),
        ),
        (
            batch(item(1, APPROVED), item(2)),
            DecideAction("proposal", 1, DISCARDED),
            BatchStatus.PENDING,
            [APPROVED, PENDING],
            (),
        ),
        (
            batch(item(1)),
            DecideAction("proposal", 99, APPROVED),
            BatchStatus.PENDING,
            [PENDING],
            (),
        ),
        (
            batch(item(1), status=BatchStatus.CANCELLED),
            DecideAction("proposal", 1, APPROVED),
            BatchStatus.CANCELLED,
            [PENDING],
            (),
        ),
        (
            batch(item(1), item(2)),
            InterruptAction(INTERRUPTED),
            BatchStatus.CANCELLED,
            [DISCARDED, DISCARDED],
            (
                RejectPendingEffect((("proposal", 1), ("proposal", 2))),
                ResolveCallsEffect(("call-1", "call-2"), DISCARDED, INTERRUPTED),
            ),
        ),
        (
            batch(item(1, APPROVED), item(2)),
            InterruptAction(INTERRUPTED),
            BatchStatus.CANCELLED,
            [APPROVED, DISCARDED],
            (
                RejectPendingEffect((("proposal", 2),)),
                ResolveCallsEffect(("call-2",), DISCARDED, INTERRUPTED),
            ),
        ),
        (
            batch(item(1, APPROVED)),
            InterruptAction(INTERRUPTED),
            BatchStatus.CANCELLED,
            [APPROVED],
            (),
        ),
    ],
)
def test_a_batch_moves_only_the_way_the_table_says(
    state, action, expected_status, expected_decisions, expected_effects
):
    updated, effects = reduce(state, action)

    assert updated.status is expected_status
    assert [candidate.decision for candidate in updated.items] == expected_decisions
    assert effects == expected_effects


def test_the_head_is_the_first_screen_still_waiting_for_an_answer():
    state = batch(item(1, APPROVED), item(2), item(3))

    assert state.head == item(2)

    decided, _effects = reduce(state, DecideAction("proposal", 2, DISCARDED))

    assert decided.head == item(3)


def test_a_batch_with_nothing_left_to_answer_has_no_head():
    assert batch(item(1, APPROVED), item(2, DISCARDED)).head is None
