"""Saved Requests: a Card query the owner reruns from the interface."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...foundation.screens import MenuButton, ScreenCommand, ScreenSpec
from . import agent, proposal, telegram, views
from .model import SavedRequest
from .telegram import REQUEST_CALLBACK_ACTIONS, command_requests, render_saved_request

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
            menu=MenuButton("🔎 Requests", row=4),
        ),
    ),
    callback_actions=REQUEST_CALLBACK_ACTIONS,
)
