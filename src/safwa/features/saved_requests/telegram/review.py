"""How a proposed Request reads to the owner, and how a saved one reads when it is cited."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.proposals.api import (
    ProposalChange,
    ProposalScreen,
    ShownField,
)
from tg_agent_shell.proposals.marks import shown
from tg_agent_shell.proposals.render import (
    detail_lines,
    named_details,
    named_summary,
)
from tg_agent_shell.telegram import citation_title, with_citation_fields

from ..api import request_cards
from ..model import SavedRequest

# A Request is its query, and the owner reads that column as SQL.
REQUEST_LABELS = {"query_sql": "SQL"}


class RequestProposalPresenter:
    entity = "request"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values), REQUEST_LABELS)

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        fallback_lines = self.raw_details(fallback) if fallback is not None else []
        return await named_details(
            session, change, fallback_lines, model=SavedRequest, labels=REQUEST_LABELS
        )

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        return await named_summary(session, change, details, model=SavedRequest)

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        first = changes[0]
        saved = await session.get(SavedRequest, first.entity_id) if first.entity_id else None
        current = (
            {
                "name": saved.name,
                "description": saved.description,
                "query_sql": saved.query_sql,
            }
            if saved is not None
            else {}
        )
        proposed = dict(current)
        for change in changes:
            proposed.update(change.values)
        # The query reads exactly as Save will store it (SR-AI-007).
        return ProposalScreen(
            item="Request",
            fields=tuple(
                ShownField(
                    label, shown(current.get(key)), shown(proposed.get(key)), anchor=key == "name"
                )
                for key, label in (
                    ("name", "Name"),
                    ("description", "Description"),
                    ("query_sql", REQUEST_LABELS["query_sql"]),
                )
            ),
        )


async def request_citation_label(
    session: AsyncSession, services: Any, request: SavedRequest, *, short: bool
) -> str:
    """A Request is named by how many Cards it currently matches, not by its SQL."""
    matches = await request_cards(session, request.query_sql, services.views)
    return with_citation_fields(
        f"💬 {citation_title(request.name, short=short)}", [str(len(matches))]
    )
