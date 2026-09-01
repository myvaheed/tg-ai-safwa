"""Cards: the tree the owner keeps."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import MenuButton, ScreenCommand, ScreenSpec
from . import agent, proposal, telegram, views
from .model import Card
from .telegram import (
    CARD_CALLBACK_ACTIONS,
    CARD_TEXT_INPUTS,
    card_citation_label,
    command_backlog,
    render_card,
    start_manual_card_creation,
)

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
            label=card_citation_label,
        ),
    ),
    commands=(
        ScreenCommand(
            handler=command_backlog,
            command="backlog",
            description="Backlog dashboard",
            nav="backlog",
            menu=MenuButton("📚 Backlog", row=2),
        ),
        # Add is a menu button and nothing else: a Card is created on a screen.
        ScreenCommand(
            handler=start_manual_card_creation, nav="add", menu=MenuButton("➕ Add", row=2)
        ),
    ),
    callback_actions=CARD_CALLBACK_ACTIONS,
    text_inputs=CARD_TEXT_INPUTS,
)
