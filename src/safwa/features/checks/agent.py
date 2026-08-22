"""The Check mutation tool. The board subagent owns the turn that calls it."""

from __future__ import annotations

from ...ai.contracts import CheckToolInput
from ..proposals.api import MutationToolSpec, entity_change

CHECK_TOOL = MutationToolSpec(
    name="check",
    input_model=CheckToolInput,
    description=(
        "Propose one Check — a state observation on a Card. Answer one only when the user "
        "already said how it went; otherwise cite it and let them. Use mode=link or "
        "mode=unlink to put a Value on this Check or take it off."
    ),
    to_change=entity_change("check"),
)
