"""Checks: one state observation, answered once."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import ScreenSpec
from . import agent, proposal, telegram, views
from .model import Check
from .telegram import CHECK_CALLBACK_ACTIONS, render_check

MODULE = FeatureModule(
    name="checks",
    proposals=(
        ProposalContribution(
            handler=proposal.CheckProposalHandler(),
            tool=agent.CHECK_TOOL,
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
