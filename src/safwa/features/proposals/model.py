"""What one proposal is on disk, the batch of screens that suspends a turn, and the
frozen state that batch moves through."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...enums import ProposalStatus
from ...foundation.models import Base, TimestampMixin, UtcDateTime


class ChangeProposal(Base, TimestampMixin):
    __tablename__ = "change_proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=ProposalStatus.PENDING.value)
    workspace_revision: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class ProposalChange(Base):
    __tablename__ = "proposal_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("change_proposals.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    entity: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    expected_version: Mapped[int | None] = mapped_column(Integer)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ApprovalBatch(Base, TimestampMixin):
    """The screens one suspended turn opened, and what has been decided about them.

    The batch is the process, not a line of the agent's transcript: `status` is a column
    so finding the live one is a query rather than a walk through stored JSON.  `queue`
    holds the ordered targets and `tool_calls` the model's calls with their results; what
    the session must remember in order to continue lives on its own row.
    """

    __tablename__ = "approval_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    repair_exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    queue: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class BatchStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BatchDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DISCARDED = "discarded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One screen the owner still has, or the decision that closed it."""

    target_type: str
    target_id: int
    call_ids: tuple[str, ...]
    decision: BatchDecision = BatchDecision.PENDING

    @property
    def target(self) -> tuple[str, int]:
        return self.target_type, self.target_id


@dataclass(frozen=True, slots=True)
class BatchState:
    """Where the batch stands: its screens, in the order Safwa opened them."""

    status: BatchStatus
    items: tuple[QueueItem, ...]
    repair_exhausted: bool = False

    def item_for(self, target_type: str, target_id: int) -> QueueItem | None:
        return next((item for item in self.items if item.target == (target_type, target_id)), None)

    @property
    def head(self) -> QueueItem | None:
        return next(
            (item for item in self.items if item.decision is BatchDecision.PENDING), None
        )


@dataclass(frozen=True, slots=True)
class DecideAction:
    """The owner answered one screen: saved it, discarded it, or it could not be applied."""

    target_type: str
    target_id: int
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

    targets: tuple[tuple[str, int], ...]


# An effect is a row to write, never a reading of the state returned beside it: which
# screen comes next is `head` and whether the batch closed is `status`.
BatchEffect = ResolveCallsEffect | RejectPendingEffect
