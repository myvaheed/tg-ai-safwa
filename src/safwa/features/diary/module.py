"""The Diary: one entry per calendar date, written in the owner's voice."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram, views
from .model import DiaryEntry

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
    screens=(
        ScreenSpec(
            item_type="diary",
            model=DiaryEntry,
            open=telegram.render_diary,
            label=telegram.diary_citation_label,
        ),
    ),
)
