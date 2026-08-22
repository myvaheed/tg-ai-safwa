"""Checks: one state observation, answered once."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from . import agent, proposal, telegram, views

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
)
