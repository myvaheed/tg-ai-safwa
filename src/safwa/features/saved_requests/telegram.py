"""How a Request reads to the owner: its review screen, and the screen a citation opens."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.contracts import AgentChange
from ...models import SavedRequest
from ...telegram._presentation import short_citation_title, with_citation_fields
from ..proposals.api import (
    ProposalChange,
    ProposalScreen,
    detail_lines,
    named_details,
    named_summary,
)
from .use_cases import request_cards


class RequestProposalPresenter:
    entity = "request"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values))

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        fallback_lines = self.raw_details(fallback) if fallback is not None else []
        return await named_details(session, change, fallback_lines, model=SavedRequest)

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        return await named_summary(session, change, details, model=SavedRequest)

    async def screen(
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        # A Request is its SQL; the generic change list prints it without a second shape.
        return None


async def request_citation_label(
    session: AsyncSession, services: Any, request: SavedRequest
) -> str:
    """A Request is named by how many Cards it currently matches, not by its SQL."""
    matches = await request_cards(session, request.query_sql, services.views)
    return with_citation_fields(
        f"💬 {short_citation_title(request.name)}", [str(len(matches))]
    )
