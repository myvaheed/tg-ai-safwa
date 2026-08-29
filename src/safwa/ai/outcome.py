"""What one turn produced, in the shape the interface reads.

The runtime carries this back untouched inside a `TurnOutcome`; only Safwa looks at it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agent_runtime import TurnOutcome


class AIOutcomeKind(StrEnum):
    ANSWER = "answer"
    PROPOSAL = "proposal"


@dataclass
class AIOutcome:
    kind: AIOutcomeKind
    message: str
    proposal_id: int | None = None
    did: list[str] = field(default_factory=list)
    # The item `open` resolved, as a deep-link payload: the chat shows its screen after
    # the answer.
    open_item: str | None = None


def as_turn(outcome: AIOutcome) -> TurnOutcome:
    """The three facts the runtime needs about a finished turn, plus the outcome itself."""
    return TurnOutcome(
        message=outcome.message,
        waiting=outcome.kind is AIOutcomeKind.PROPOSAL,
        did=outcome.did,
        payload=outcome,
    )
