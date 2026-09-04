"""How a proposed Value reads to the owner, and how a saved one reads when it is cited."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.proposals.api import NamedItemPresenter
from tg_agent_shell.telegram import short_citation_title

from ..model import Value


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
