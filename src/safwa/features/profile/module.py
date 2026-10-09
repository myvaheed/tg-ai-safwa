"""The owner's Profile, edited on its screen or, through the profile subagent, in words."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram
from .hooks import DAILY_SUMMARY_HOOK as DAILY_SUMMARY_HOOK
from .telegram import PROFILE_CALLBACK_ACTIONS, command_profile
from .telegram.review import ProfileProposalPresenter

MODULE = FeatureModule(
    name="profile",
    agents=(agent.PROFILE_AGENT,),
    proposals=(
        ProposalContribution(
            handler=proposal.ProfileProposalHandler(),
            tool=agent.PROFILE_TOOL,
            presenter=ProfileProposalPresenter(),
        ),
    ),
    commands=(
        # The Profile is one tap away in the menu, so it needs no command line too.
        ScreenCommand(handler=command_profile, nav="profile", title="⚙️ Profile"),
    ),
    callback_actions=PROFILE_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT, telegram.SECRET_WORD_INPUT),
)
