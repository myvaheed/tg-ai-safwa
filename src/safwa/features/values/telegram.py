"""How a proposed Value reads to the owner."""

from __future__ import annotations

from typing import Any

from ..proposals.api import (
    NamedItemPresenter,
)
from .model import Value


class ValueProposalPresenter(NamedItemPresenter):
    entity = "value"
    model = Value
    label = "Value"

    def _current(self, item: Any) -> dict[str, Any]:
        return {
            "name": item.name,
            "description": item.description,
            "active": item.active,
        }
