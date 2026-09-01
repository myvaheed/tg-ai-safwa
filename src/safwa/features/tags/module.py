"""Tags: labels for finding Cards again."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import MenuButton, ScreenCommand, ScreenSpec
from . import agent, proposal, telegram, views
from .model import Tag
from .telegram import TAG_CALLBACK_ACTIONS, command_tags

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
    commands=(
        ScreenCommand(
            handler=command_tags,
            command="tags",
            description="Manage Tags",
            nav="tags",
            menu=MenuButton("🏷 Tags", row=3),
        ),
    ),
    callback_actions=TAG_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
