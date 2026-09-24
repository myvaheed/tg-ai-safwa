"""How a proposal to stop the onboarding is checked and then saved.

There is no entity: Save turns the tips' switch off through the same operation the Profile
switch uses, which takes the tips still owed with it.
"""

from __future__ import annotations

from typing import Any

from tg_agent_shell.proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
)

from ..profile.api import hook_switched_on, set_hook_switch
from .hooks import NOTICE_HOOK, ONBOARDING_HOOK


class OnboardingProposalHandler:
    entity = "onboarding"
    version_model: type[Any] | None = None

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        if not await hook_switched_on(context.session, ONBOARDING_HOOK.name):
            raise ToolPreparationError(
                "already_off",
                "The onboarding is already off.",
                "Tell the user onboarding is already off; the switch is in Profile. Propose nothing.",
            )
        return PreparedChange(values=dict(change.values), expected_version=None)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        # Harmless when the owner turned it off by hand since it was proposed.
        await set_hook_switch(
            context.session, ONBOARDING_HOOK.name, on=False, followers=(NOTICE_HOOK.name,)
        )
        return []
