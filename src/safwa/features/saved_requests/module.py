"""Saved Requests: a Card query the owner reruns from the interface."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import ScreenCommand, ScreenSpec
from ...telegram.callbacks import REQUEST_CALLBACK_ACTIONS
from ...telegram.commands import command_requests
from ...telegram.items import render_saved_request
from . import agent, proposal, telegram, views
from .model import SavedRequest

MODULE = FeatureModule(
    name="saved_requests",
    proposals=(
        ProposalContribution(
            handler=proposal.RequestProposalHandler(),
            tool=agent.REQUEST_TOOL,
            presenter=telegram.RequestProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
    screens=(
        ScreenSpec(
            item_type="request",
            model=SavedRequest,
            open=render_saved_request,
            label=telegram.request_citation_label,
        ),
    ),
    commands=(
        ScreenCommand(
            handler=command_requests,
            command="requests",
            description="Saved AI Requests",
            nav="requests",
        ),
    ),
    callback_actions=REQUEST_CALLBACK_ACTIONS,
)
