"""Values: what the owner cares about, and what is in focus."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import ScreenCommand, ScreenSpec
from ...telegram.callbacks import VALUE_CALLBACK_ACTIONS
from ...telegram.commands import command_values
from . import agent, proposal, telegram, views
from .model import Value

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
        ),
    ),
    callback_actions=VALUE_CALLBACK_ACTIONS,
)
