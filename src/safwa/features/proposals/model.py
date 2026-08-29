"""What one proposal is while the owner is looking at it, the batch of screens that
suspends a turn, and the frozen state that batch moves through.

None of this is stored. A review exists while the process that opened it is running and
the owner has not answered it; a restart is what ends every one of them. What survives is
what the review *did*: the rows its handlers wrote, and the turn's own `agent_runs` record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ChangeAction(StrEnum):
    """What one change does. Every mutation tool's `mode` is one of these, spelled the same.

    The tools declare their own subset, so a tool offers only the actions its entity has.
    """

    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    COMPLETE = "complete"
    CANCEL = "cancel"
    REOPEN = "reopen"
    ARCHIVE = "archive"
    DELETE = "delete"
    LINK = "link"
    UNLINK = "unlink"


@dataclass(slots=True)
class ProposalChange:
    """One validated edit, resolved against live data and waiting to be applied."""

    # The entity name is whatever feature owns it; the registry is what rejects an
    # unknown one, so this stays a plain string.
    entity: str
    action: ChangeAction
    entity_id: int | None = None
    expected_version: int | None = None
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChangeProposal:
    """One review the owner still has open, and the edits Save would carry out.

    `changes` is an ordered list because a proposal keeps the right to hold several edits;
    nothing writes a second one today, and the screen already lists them all.
    """

    id: int
    message: str
    workspace_revision: int
    changes: list[ProposalChange] = field(default_factory=list)


class BatchDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DISCARDED = "discarded"
    FAILED = "failed"


# The interface owns this line, never the model. History replays it as a tool result
# rather than as words Safwa said, so each one also says what it meant.
SAVED_RECEIPT = "✅ Saved"
AUTO_SAVED_RECEIPT = "⚡ Auto-saved"
DISCARDED_RECEIPT = "🗑 Discarded"
FAILED_RECEIPT = "⚠️ Failed"

DECISION_RECEIPTS = {
    BatchDecision.APPROVED: SAVED_RECEIPT,
    BatchDecision.DISCARDED: DISCARDED_RECEIPT,
    BatchDecision.FAILED: FAILED_RECEIPT,
}

RECEIPT_MEANINGS = {
    SAVED_RECEIPT: "applied",
    AUTO_SAVED_RECEIPT: "applied",
    DISCARDED_RECEIPT: "not applied, the user rejected it",
    FAILED_RECEIPT: "not applied, it failed",
}


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One screen the owner still has, or the decision that closed it."""

    proposal_id: int
    call_ids: tuple[str, ...]
    decision: BatchDecision = BatchDecision.PENDING


@dataclass(frozen=True, slots=True)
class BatchState:
    """Where the batch stands: its screens, in the order Safwa opened them.

    There is no separate status: a batch is over exactly when no screen is still waiting,
    which is `head is None`.
    """

    items: tuple[QueueItem, ...]
    repair_exhausted: bool = False

    def item_for(self, proposal_id: int) -> QueueItem | None:
        return next((item for item in self.items if item.proposal_id == proposal_id), None)

    @property
    def head(self) -> QueueItem | None:
        return next(
            (item for item in self.items if item.decision is BatchDecision.PENDING), None
        )


@dataclass(slots=True)
class ApprovalBatch:
    """The screens one suspended turn opened, and what has been decided about them.

    `state` is replaced whole rather than edited, so a transition is one assignment of what
    `reduce` returned. `tool_calls` are the model's calls with their results, and they are
    the one part of a batch that outlives it: they are folded into the session's own record.
    """

    run_id: int
    tool_calls: list[dict[str, Any]]
    state: BatchState


@dataclass(frozen=True, slots=True)
class DecideAction:
    """The owner answered one screen: saved it, discarded it, or it could not be applied."""

    proposal_id: int
    decision: BatchDecision


@dataclass(frozen=True, slots=True)
class InterruptAction:
    """New dialogue arrived over the screens, so the batch never gets its answer."""

    reason: str


BatchAction = DecideAction | InterruptAction


@dataclass(frozen=True, slots=True)
class ResolveCallsEffect:
    """Hand these tool calls their result: the model waits on every one of them."""

    call_ids: tuple[str, ...]
    decision: BatchDecision
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RejectPendingEffect:
    """The screens that lost their answer, to be recorded as discarded."""

    proposal_ids: tuple[int, ...]


# An effect is a review to end, never a reading of the state returned beside it: which
# screen comes next, and whether the batch closed, are both `head`.
BatchEffect = ResolveCallsEffect | RejectPendingEffect
