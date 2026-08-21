"""The Request mutation tool. The board subagent owns the turn that calls it."""

from __future__ import annotations

from ...ai.contracts import RequestToolInput
from ..proposals.api import MutationToolSpec, entity_change

REQUEST_TOOL = MutationToolSpec(
    name="request",
    input_model=RequestToolInput,
    description="Propose one Request — a saved query over ai_cards the user reruns.",
    to_change=entity_change("request"),
)
