"""How a proposed Value reads to the owner, and how a saved one reads when it is cited."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.proposals.api import ProposalChange, ProposalScreen, ShownField
from tg_agent_shell.proposals.marks import shown
from tg_agent_shell.proposals.render import NamedItemPresenter
from tg_agent_shell.telegram import citation_title

from ..model import Value


class ValueProposalPresenter(NamedItemPresenter):
    entity = "value"
    model = Value
    label = "Value"

    def _current(self, item: Any) -> dict[str, Any]:
        return {
            "name": item.name,
            "description": item.description,
            "order": item.order,
        }

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        screen = await super().screen(session, changes)
        if screen is None or not any("order" in change.values for change in changes):
            return screen
        first = changes[0]
        item = await session.get(Value, first.entity_id) if first.entity_id else None
        before = item.order if item is not None else None
        after = before
        for change in changes:
            after = change.values.get("order", after)
        return replace(
            screen, fields=(*screen.fields, ShownField("Order", shown(before), shown(after)))
        )


async def value_citation_label(
    session: AsyncSession, services: Any, value: Value, *, short: bool
) -> str:
    return f"💎 {citation_title(value.name, short=short)}"
