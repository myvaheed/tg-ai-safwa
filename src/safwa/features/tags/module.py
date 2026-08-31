"""Tags: labels for finding Cards again."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import ScreenSpec
from . import agent, proposal, telegram, views
from .model import Tag

MODULE = FeatureModule(
    name="tags",
    proposals=(
        ProposalContribution(
            handler=proposal.TagProposalHandler(),
            tool=agent.TAG_TOOL,
            presenter=telegram.TagProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
    screens=(
        ScreenSpec(
            item_type="tag",
            model=Tag,
            open=telegram.open_tag,
            label=telegram.tag_citation_label,
        ),
    ),
)
