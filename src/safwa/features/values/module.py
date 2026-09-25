"""Values: what the owner cares about, and what is in focus."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.proposals.api import SimilarItems
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram, views
from .model import Value
from .telegram import VALUE_CALLBACK_ACTIONS, command_values

MODULE = FeatureModule(
    name="values",
    proposals=(
        ProposalContribution(
            handler=proposal.ValueProposalHandler(),
            tool=agent.VALUE_TOOL,
            autoapprovals=agent.VALUE_AUTOAPPROVALS,
            presenter=telegram.ValueProposalPresenter(),
            similar=SimilarItems(field="name", open_items=proposal.every_value),
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
