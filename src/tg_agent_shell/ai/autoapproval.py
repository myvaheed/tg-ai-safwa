"""What a feature declares may be saved without the owner seeing it.

The declaration is data. What reads a proposal against the owner's words is a hook,
`AUTOAPPROVAL_HOOK` in `proposals/hooks.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AutoApprovalRule:
    """One action a feature says may be saved without the owner ever seeing it.

    Declaring one is a data change and nothing else: name the action, and for an update
    name the fields that action may alter.  A create is never declared — a new item is the
    one change the owner cannot read as a correction of something they already know, so
    every create takes the review screen.
    """

    criteria: str
    allowed_fields: frozenset[str] | None = None

    def accepts(self, values: Mapping[str, Any]) -> bool:
        return self.allowed_fields is None or set(values).issubset(self.allowed_fields)


# How a rule is read, so a feature naming its own fields does not also write out how they
# are to be judged.
SCALAR_UPDATE = (
    "Approve only when every changed field and its exact new value are clearly requested. "
    "Do not infer an additional edit from what would merely be useful."
)
RELATIONSHIP_LINK = (
    "Approve when this proposal links exactly the relationship type and referenced items requested. "
    "The creation or editing of those items may be handled by separate proposals."
)
