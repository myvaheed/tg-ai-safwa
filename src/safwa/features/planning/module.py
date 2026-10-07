"""Planning: the mode the workspace is in when no Sprint runs, and the Sprint itself —
run on its screen or, through the sprint subagent, in words."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram, views
from .hooks import KEY_ACTIONS_HOOK as KEY_ACTIONS_HOOK
from .hooks import KEY_WARNING_HOOK as KEY_WARNING_HOOK
from .hooks import SPRINT_END_HOOK as SPRINT_END_HOOK
from .hooks import SPRINT_EXPIRY_HOOK as SPRINT_EXPIRY_HOOK
from .hooks import SPRINT_SUMMARY_HOOK as SPRINT_SUMMARY_HOOK
from .telegram import PLANNING_CALLBACK_ACTIONS, render_sprint
from .telegram.review import SprintProposalPresenter

MODULE = FeatureModule(
    name="planning",
    agents=(agent.SPRINT_AGENT,),
    proposals=(
        ProposalContribution(
            handler=proposal.SprintProposalHandler(),
            tool=agent.SPRINT_TOOL,
            presenter=SprintProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
    commands=(
        ScreenCommand(
            handler=render_sprint,
            command="sprint",
            description="Planning or Sprint",
            nav="sprint",
            title="🏃 Sprint",
        ),
    ),
    callback_actions=PLANNING_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
