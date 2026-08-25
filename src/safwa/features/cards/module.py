"""Cards: the tree the owner keeps."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from . import agent, proposal, telegram, views

MODULE = FeatureModule(
    name="cards",
    proposals=(
        ProposalContribution(
            handler=proposal.CardProposalHandler(),
            tool=agent.CARD_TOOL,
            presenter=telegram.CardProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
)
