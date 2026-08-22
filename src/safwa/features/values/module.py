"""Values: what the owner cares about, and what is in focus."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from . import agent, proposal, telegram, views

MODULE = FeatureModule(
    name="values",
    proposals=(
        ProposalContribution(
            handler=proposal.ValueProposalHandler(),
            tool=agent.VALUE_TOOL,
            presenter=telegram.ValueProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
)
