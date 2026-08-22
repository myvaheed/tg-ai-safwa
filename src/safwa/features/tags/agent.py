"""The Tag mutation tool. The board subagent owns the turn that calls it."""

from __future__ import annotations

from ...ai.contracts import TagToolInput
from ..proposals.api import MutationToolSpec, entity_change

TAG_TOOL = MutationToolSpec(
    name="tag",
    input_model=TagToolInput,
    description="Propose one Tag — a free label for finding Cards.",
    to_change=entity_change("tag"),
)
