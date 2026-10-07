"""Onboarding: a tip after each created or finished item, one notice before the first
answer, what still stands when the owner writes after a long break, and a subagent that
explains Safwa. Profile proposes to stop the onboarding."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from ..profile.agent import ONBOARDING_AUTOAPPROVALS, STOP_ONBOARDING_TOOL
from . import agent, proposal, telegram
from .hooks import NOTICE_HOOK as NOTICE_HOOK
from .hooks import ONBOARDING_HOOK as ONBOARDING_HOOK
from .hooks import PRESENCE_HOOK as PRESENCE_HOOK
from .hooks import RETURN_HOOK as RETURN_HOOK

MODULE = FeatureModule(
    name="onboarding",
    agents=(agent.ONBOARDING_AGENT,),
    proposals=(
        ProposalContribution(
            handler=proposal.OnboardingProposalHandler(),
            tool=STOP_ONBOARDING_TOOL,
            presenter=telegram.OnboardingProposalPresenter(),
            autoapprovals=ONBOARDING_AUTOAPPROVALS,
        ),
    ),
)
