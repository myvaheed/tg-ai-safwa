"""How a proposed Tag reads to the owner."""

from __future__ import annotations

from typing import Any

from ..proposals.api import (
    NamedItemPresenter,
)
from .model import Tag


class TagProposalPresenter(NamedItemPresenter):
    entity = "tag"
    model = Tag
    label = "Tag"

    def _current(self, item: Any) -> dict[str, Any]:
        return {"name": item.name, "description": item.description}
