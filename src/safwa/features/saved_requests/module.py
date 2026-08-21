"""Saved Requests: a Card query the owner reruns from the interface."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from . import agent, proposal, telegram, views

MODULE = FeatureModule(
    name="saved_requests",
    proposals=(
        ProposalContribution(
            handler=proposal.RequestProposalHandler(),
            tool=agent.REQUEST_TOOL,
            presenter=telegram.RequestProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
)
