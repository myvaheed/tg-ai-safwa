"""How a Value reads to the owner: its review screen, and the screen a citation opens."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...telegram._presentation import short_citation_title
from ...telegram.items import render_item_editor
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


async def value_citation_label(session: AsyncSession, services: Any, value: Value) -> str:
    return f"💎 {short_citation_title(value.name)}"


async def open_value(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    await render_item_editor(
        message, services, "value", mode="view", item_id=item_id, replace=replace
    )
