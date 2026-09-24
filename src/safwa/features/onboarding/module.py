"""Onboarding: a tip after each created or finished item, one notice before the first
answer, and a subagent that explains Safwa and proposes to stop."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram
from .hooks import NOTICE_HOOK as NOTICE_HOOK
from .hooks import ONBOARDING_HOOK as ONBOARDING_HOOK

MODULE = FeatureModule(
    name="onboarding",
    agents=(agent.ONBOARDING_AGENT,),
    proposals=(
        ProposalContribution(
            handler=proposal.OnboardingProposalHandler(),
            tool=agent.STOP_ONBOARDING_TOOL,
            presenter=telegram.OnboardingProposalPresenter(),
            autoapprovals=agent.ONBOARDING_AUTOAPPROVALS,
        ),
    ),
)
