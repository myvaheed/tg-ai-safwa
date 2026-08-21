"""How a proposed Request reads to the owner."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.contracts import AgentChange
from ...models import ProposalChange, SavedRequest
from ..proposals.api import (
    ProposalScreen,
    detail_lines,
    named_details,
    named_summary,
)


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
