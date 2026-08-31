"""Values: what the owner cares about, and what is in focus."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import ScreenSpec
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
)
