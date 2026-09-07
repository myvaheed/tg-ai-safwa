"""The ledger, as this application registers it."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram, views
from .model import Entry

MODULE = FeatureModule(
    name="ledger",
    agents=(agent.BOOKKEEPER,),
    proposals=(
        ProposalContribution(
            handler=proposal.EntryProposalHandler(),
            tool=agent.ENTRY_TOOL,
            presenter=telegram.EntryProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
    screens=(
        ScreenSpec(
            item_type="entry",
            model=Entry,
            open=telegram.render_entry,
            label=telegram.entry_citation_label,
        ),
    ),
)
