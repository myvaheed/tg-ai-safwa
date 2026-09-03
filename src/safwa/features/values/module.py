"""Values: what the owner cares about, and what is in focus."""

from __future__ import annotations

from ...foundation.screens import ScreenCommand, ScreenSpec
from ...shell.manifest import FeatureModule, ProposalContribution
from . import agent, proposal, telegram, views
from .model import Value
from .telegram import VALUE_CALLBACK_ACTIONS, command_values

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
    screens=(
        ScreenSpec(
            item_type="value",
            model=Value,
            open=telegram.open_value,
            label=telegram.value_citation_label,
        ),
    ),
    commands=(
        ScreenCommand(
            handler=command_values,
            command="values",
            description="Values in focus",
            nav="values",
            title="💎 Values",
        ),
    ),
    callback_actions=VALUE_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
