"""Saved Requests: a Card query the owner reruns from the interface."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram, views
from .model import SavedRequest
from .telegram import REQUEST_CALLBACK_ACTIONS, command_requests, render_saved_request

MODULE = FeatureModule(
    name="saved_requests",
    proposals=(
        ProposalContribution(
            handler=proposal.RequestProposalHandler(),
            tool=agent.REQUEST_TOOL,
            autoapprovals=agent.REQUEST_AUTOAPPROVALS,
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
            title="🔎 Requests",
        ),
    ),
    callback_actions=REQUEST_CALLBACK_ACTIONS,
)
