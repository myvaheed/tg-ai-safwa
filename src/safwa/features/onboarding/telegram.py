"""How a proposal to stop the onboarding reads to the owner."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.proposals.api import ProposalChange, ProposalScreen

TURN_OFF = "Turn onboarding off"
DETAILS = ["Onboarding: off"]


class OnboardingProposalPresenter:
    entity = "onboarding"

    def raw_details(self, change: AgentChange) -> list[str]:
        return list(DETAILS)

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        return list(DETAILS)

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        return TURN_OFF

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        return ProposalScreen(
            mode="Turn off",
            item="onboarding",
            blocks=(
                "No more tips after you create or finish something. Questions about Safwa "
                "are still answered.\nTurn it back on in ⚙️ Profile.",
            ),
        )
