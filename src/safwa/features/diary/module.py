"""The Diary: one entry per calendar date, written in the owner's voice."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...domain import sync_diary_reminder
from . import agent, proposal, telegram, views

MODULE = FeatureModule(
    name="diary",
    agents=(agent.DIARY_AGENT,),
    proposals=(
        ProposalContribution(
            handler=proposal.DiaryProposalHandler(),
            tool=agent.DIARY_TOOL,
            presenter=telegram.DiaryProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
    recover=sync_diary_reminder,
)
