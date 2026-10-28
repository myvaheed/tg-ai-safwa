"""Every transition of a batch of review screens, as a table.

The reducer is where "what happens when the owner answers a screen" is decided, so each
row here is one press and what it leaves behind. A Save is kept, with no effect: writing
the kept Saves once no screen is left, and resuming the session, are the caller's, and
none of them is needed to check a transition.
"""

from __future__ import annotations

import pytest

from tg_agent_shell.proposals.model import (
    BatchDecision,
    BatchState,
    DecideAction,
    InterruptAction,
    QueueItem,
    RejectPendingEffect,
    ResolveCallsEffect,
)
from tg_agent_shell.proposals.reducer import EXPIRED as UNANSWERED
from tg_agent_shell.proposals.reducer import INTERRUPTED, NOT_SHOWN, reduce

PENDING = BatchDecision.PENDING
APPROVED = BatchDecision.APPROVED
DISCARDED = BatchDecision.DISCARDED
FAILED = BatchDecision.FAILED
EXPIRED = BatchDecision.EXPIRED


def item(proposal_id: int, decision: BatchDecision = PENDING) -> QueueItem:
    return QueueItem(
        proposal_id=proposal_id,
        call_ids=(f"call-{proposal_id}",),
        decision=decision,
    )


def batch(*items: QueueItem, exhausted: bool = False):
    return BatchState(items=items, repair_exhausted=exhausted)


@pytest.mark.parametrize(
    ("state", "action", "expected_decisions", "expected_effects"),
    [
        (
            batch(item(1)),
            DecideAction(1, APPROVED),
            [APPROVED],
            (),
        ),
        (
            batch(item(1), item(2)),
            DecideAction(1, APPROVED),
            [APPROVED, PENDING],
            (),
        ),
        # PR-SAVE-010: a Discard ends the queue, and the screens behind it are never shown.
        (
            batch(item(1), item(2)),
            DecideAction(1, DISCARDED),
            [DISCARDED, DISCARDED],
            (
                ResolveCallsEffect(("call-1",), DISCARDED),
                RejectPendingEffect((2,)),
                ResolveCallsEffect(("call-2",), DISCARDED, NOT_SHOWN),
            ),
        ),
        (
            batch(item(1, APPROVED), item(2), item(3)),
            DecideAction(2, DISCARDED),
            [APPROVED, DISCARDED, DISCARDED],
            (
                ResolveCallsEffect(("call-2",), DISCARDED),
                RejectPendingEffect((3,)),
                ResolveCallsEffect(("call-3",), DISCARDED, NOT_SHOWN),
            ),
        ),
        (
            batch(item(1, APPROVED), item(2)),
            DecideAction(2, FAILED),
            [APPROVED, FAILED],
            (ResolveCallsEffect(("call-2",), FAILED),),
        ),
        (
            batch(item(1, APPROVED), item(2), exhausted=True),
            DecideAction(2, APPROVED),
            [APPROVED, APPROVED],
            (),
        ),
        (
            batch(item(1, APPROVED), item(2)),
            DecideAction(1, DISCARDED),
            [APPROVED, PENDING],
            (),
        ),
        (
            batch(item(1)),
            DecideAction(99, APPROVED),
            [PENDING],
            (),
        ),
        (
            batch(item(1), item(2)),
            InterruptAction(INTERRUPTED),
            [DISCARDED, DISCARDED],
            (
                RejectPendingEffect((1, 2)),
                ResolveCallsEffect(("call-1", "call-2"), DISCARDED, INTERRUPTED),
            ),
        ),
        (
            batch(item(1, APPROVED), item(2)),
            InterruptAction(INTERRUPTED),
            [APPROVED, DISCARDED],
            (
                RejectPendingEffect((2,)),
                ResolveCallsEffect(("call-2",), DISCARDED, INTERRUPTED),
            ),
        ),
        (
            batch(item(1, APPROVED)),
            InterruptAction(INTERRUPTED),
            [APPROVED],
            (),
        ),
        # PR-EXPIRE-029: the same closing, recorded as expired rather than discarded.
        (
            batch(item(1, APPROVED), item(2), item(3)),
            InterruptAction(UNANSWERED, EXPIRED),
            [APPROVED, EXPIRED, EXPIRED],
            (
                RejectPendingEffect((2, 3)),
                ResolveCallsEffect(("call-2", "call-3"), EXPIRED, UNANSWERED),
            ),
        ),
    ],
)
def test_a_batch_moves_only_the_way_the_table_says(
    state, action, expected_decisions, expected_effects
):
    updated, effects = reduce(state, action)

    assert [candidate.decision for candidate in updated.items] == expected_decisions
    assert effects == expected_effects
    # Which screen comes next and whether the batch closed are read off the state, so an
    # effect never repeats them; what the batch was told about its repairs is carried.
    assert (updated.head is None) is all(
        decision is not PENDING for decision in expected_decisions
    )
    assert updated.repair_exhausted == state.repair_exhausted


def test_the_head_is_the_first_screen_still_waiting_for_an_answer():
    state = batch(item(1, APPROVED), item(2), item(3))

    assert state.head == item(2)

    decided, _effects = reduce(state, DecideAction(2, APPROVED))

    assert decided.head == item(3)


def test_a_kept_save_carries_its_notes_and_a_late_press_changes_nothing():
    """PR-SAVE-031 — tests/brd/tg_agent_shell/proposals.feature"""
    state = batch(item(1), item(2))
    kept, effects = reduce(state, DecideAction(1, APPROVED, {"destructive": True}))

    assert effects == () and kept.items[0].notes == {"destructive": True}
    # A press on a screen already answered hands the very same state back.
    assert reduce(kept, DecideAction(1, DISCARDED))[0] is kept
