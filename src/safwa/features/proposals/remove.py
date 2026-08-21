"""`remove` — the one tool that archives or deletes, whichever entity it is given.

It owns no entity of its own: the change it produces is handled by the feature that owns
the target, which is why it registers as a plain mutation tool.
"""

from __future__ import annotations

from pydantic import BaseModel

from ...ai.contracts import AgentChange, RemoveToolInput
from .api import MutationToolSpec


def _remove_change(call: BaseModel) -> AgentChange:
    return AgentChange(entity=call.entity, action=call.mode, id=call.id)


REMOVE_TOOL = MutationToolSpec(
    name="remove",
    input_model=RemoveToolInput,
    description="Archive or delete one item of any kind. No other tool removes anything.",
    to_change=_remove_change,
)
