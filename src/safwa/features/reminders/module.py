"""Reminders: a trigger the owner set, fired by schedule arithmetic alone."""

from __future__ import annotations

from ...bootstrap.module_manifest import FeatureModule, ProposalContribution
from ...recovery import reconcile_reminders
from . import agent, background, proposal, telegram, views

MODULE = FeatureModule(
    name="reminders",
    proposals=(
        ProposalContribution(
            handler=proposal.ReminderProposalHandler(),
            tool=agent.REMINDER_TOOL,
            presenter=telegram.ReminderProposalPresenter(),
        ),
    ),
    views=views.VIEWS,
    recover=reconcile_reminders,
    background=(background.REMINDER_SCHEDULER,),
)
