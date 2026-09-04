"""Turn one validated mutation call into the exact values a proposal stores.

Preparation checks a change against live data — the parent, named references, Pending
Checks, reminder timing, Request SQL — and normalises what survives.  It runs for
whoever authored the change and writes nothing, so a failure is one model-visible
retryable tool error rather than the end of a turn.

Those checks belong to the feature that owns the entity. What stays here is the
orchestration: the workspace, the handler lookup, and the context every handler reads.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import LlmProvider

from ..ai.contracts import AgentChange
from ..ai.sql import ReadOnlyQueryRunner
from .api import PreparationContext, PreparedChange, ProposalRegistry, World


class ChangePreparer:
    def __init__(
        self,
        provider: LlmProvider,
        query_runner: ReadOnlyQueryRunner,
        proposals: ProposalRegistry,
    ) -> None:
        self.provider = provider
        self.query_runner = query_runner
        self.proposals = proposals

    async def world(self, session: AsyncSession) -> World:
        """What this application reports as the state a proposal is made against."""
        return await self.proposals.world(session)

    async def prepare(self, session: AsyncSession, change: AgentChange) -> PreparedChange:
        context = PreparationContext(
            session=session,
            world=await self.proposals.world(session),
            provider=self.provider,
            query_runner=self.query_runner,
            views=self.proposals.views,
        )
        return await self.proposals.handler(change.entity).prepare(context, change)
