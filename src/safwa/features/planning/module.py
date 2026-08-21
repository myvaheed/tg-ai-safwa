"""Planning: Cards, Checks, Values, Tags and the Sprint they are committed to."""

from __future__ import annotations

from ...bootstrap.module_manifest import (
    FeatureModule,
    ProposalContribution,
)
from . import agent, background, proposal, telegram, views

MODULE = FeatureModule(
    name="planning",
    agents=(agent.BOARD_AGENT,),
    proposals=(
        ProposalContribution(
            handler=proposal.CardProposalHandler(),
            tool=agent.CARD_TOOL,
            presenter=telegram.CardProposalPresenter(),
        ),
        ProposalContribution(
            handler=proposal.CheckProposalHandler(),
            tool=agent.CHECK_TOOL,
            presenter=telegram.CheckProposalPresenter(),
        ),
        ProposalContribution(
            handler=proposal.ValueProposalHandler(),
            tool=agent.VALUE_TOOL,
            presenter=telegram.ValueProposalPresenter(),
        ),
        ProposalContribution(
            handler=proposal.TagProposalHandler(),
            tool=agent.TAG_TOOL,
            presenter=telegram.TagProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
    background=(background.SPRINT_EXPIRY,),
)
