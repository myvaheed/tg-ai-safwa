"""What one turn produced, in the shape the interface reads.

The runtime carries this back untouched inside a `TurnOutcome`; only the shell reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent_runtime import TurnOutcome


class AIOutcomeKind(StrEnum):
    ANSWER = "answer"
    PROPOSAL = "proposal"


@dataclass
class AIOutcome:
    kind: AIOutcomeKind
    message: str
    proposal_id: int | None = None
    # The item `open` resolved, as a deep-link payload: the chat shows its screen after
    # the answer.
    open_item: str | None = None
    # What the conversation keeps of the turn that said this, in the provider's shape:
    # its calls and their results, then the model's own words. Empty for words the
    # interface wrote itself.
    turn: tuple[dict[str, Any], ...] = ()


def as_turn(outcome: AIOutcome) -> TurnOutcome:
    """The two facts the runtime needs about a finished turn, plus the outcome itself."""
    return TurnOutcome(
        message=outcome.message,
        waiting=outcome.kind is AIOutcomeKind.PROPOSAL,
        payload=outcome,
    )
