"""Tags: labels for finding Cards again."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.proposals.api import SimilarItems
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram, views
from .model import Tag
from .telegram import TAG_CALLBACK_ACTIONS, command_tags

MODULE = FeatureModule(
    name="tags",
    proposals=(
        ProposalContribution(
            handler=proposal.TagProposalHandler(),
            tool=agent.TAG_TOOL,
            autoapprovals=agent.TAG_AUTOAPPROVALS,
            presenter=telegram.TagProposalPresenter(),
            similar=SimilarItems(field="name", open_items=proposal.every_tag),
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
            title="🏷 Tags",
        ),
    ),
    callback_actions=TAG_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
