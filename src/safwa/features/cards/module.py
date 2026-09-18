"""Cards: the tree the owner keeps."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram, views
from .hooks import BLOCKER_HOOK as BLOCKER_HOOK
from .hooks import EMPTY_PARENTS_HOOK as EMPTY_PARENTS_HOOK
from .hooks import ENERGY_BALANCE_HOOK as ENERGY_BALANCE_HOOK
from .hooks import HARD_TIME_HOOK as HARD_TIME_HOOK
from .hooks import REST_TODAY_HOOK as REST_TODAY_HOOK
from .hooks import TODAY_OVERLOAD_HOOK as TODAY_OVERLOAD_HOOK
from .model import Card
from .telegram import (
    CARD_CALLBACK_ACTIONS,
    CARD_TEXT_INPUTS,
    card_citation_label,
    command_backlog,
    command_today,
    render_card,
    start_manual_card_creation,
)

MODULE = FeatureModule(
    name="cards",
    proposals=(
        ProposalContribution(
            handler=proposal.CardProposalHandler(),
            tool=agent.CARD_TOOL,
            autoapprovals=agent.CARD_AUTOAPPROVALS,
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
        # The Backlog is one tap away in the menu, so it needs no command line too.
        ScreenCommand(handler=command_backlog, nav="backlog", title="📚 Backlog"),
        ScreenCommand(
            handler=command_today,
            command="today",
            description="Today dashboard",
            nav="today",
            title="☀️ Today",
        ),
        # Add is a menu button and nothing else: a Card is created on a screen.
        ScreenCommand(
            handler=start_manual_card_creation, nav="add", title="➕ Add"
        ),
    ),
    callback_actions=CARD_CALLBACK_ACTIONS,
    text_inputs=CARD_TEXT_INPUTS,
)
