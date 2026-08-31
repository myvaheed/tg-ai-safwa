"""Checks: one state observation, answered once."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import ScreenSpec
from ...telegram.checks import render_check
from . import agent, proposal, telegram, views
from .model import Check

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
)
