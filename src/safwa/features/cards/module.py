"""Cards: the tree the owner keeps."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import ScreenSpec
from ...telegram.cards import render_card
from . import agent, proposal, telegram, views
from .model import Card

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
    screens=(
        ScreenSpec(
            item_type="card",
            model=Card,
            open=render_card,
            label=telegram.card_citation_label,
        ),
    ),
)
