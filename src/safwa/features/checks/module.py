"""Checks: one state observation, answered once."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram, views
from .hooks import MISSED_RUN_HOOK as MISSED_RUN_HOOK
from .model import Check
from .telegram import CHECK_CALLBACK_ACTIONS, render_check

MODULE = FeatureModule(
    name="checks",
    proposals=(
        ProposalContribution(
            handler=proposal.CheckProposalHandler(),
            tool=agent.CHECK_TOOL,
            autoapprovals=agent.CHECK_AUTOAPPROVALS,
            presenter=telegram.CheckProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
    screens=(
        ScreenSpec(
            item_type="check",
            model=Check,
            open=render_check,
            label=telegram.check_citation_label,
        ),
    ),
    callback_actions=CHECK_CALLBACK_ACTIONS,
)
