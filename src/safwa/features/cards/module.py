"""Cards: the tree the owner keeps."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import ScreenCommand, ScreenSpec
from ...telegram.callbacks import CARD_CALLBACK_ACTIONS
from ...telegram.cards import render_card
from ...telegram.commands import command_add, command_backlog
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
    commands=(
        ScreenCommand(
            handler=command_backlog,
            command="backlog",
            description="Backlog dashboard",
            nav="backlog",
        ),
        # Add is a menu button and nothing else: a Card is created on a screen.
        ScreenCommand(handler=command_add, nav="add"),
    ),
    callback_actions=CARD_CALLBACK_ACTIONS,
)
