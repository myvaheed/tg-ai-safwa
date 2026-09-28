"""Reminders: a trigger the owner set, fired by schedule arithmetic alone.

The tick writes a Cue and nothing else, so it needs no adapter at all: what a Reminder says
reaches the owner through the Cue queue, like everything else Safwa says first.
"""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.proposals.api import SimilarItems
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from . import agent, proposal, telegram, views
from .hooks import REMINDER_FIRE_HOOK as REMINDER_FIRE_HOOK
from .hooks import REMINDER_START_HOOK as REMINDER_START_HOOK
from .model import Reminder
from .telegram import REMINDER_CALLBACK_ACTIONS, render_reminder, render_reminders

MODULE = FeatureModule(
    name="reminders",
    proposals=(
        ProposalContribution(
            handler=proposal.ReminderProposalHandler(),
            tool=agent.REMINDER_TOOL,
            presenter=telegram.ReminderProposalPresenter(),
            similar=SimilarItems(field="instruction", open_items=proposal.owner_reminders),
        ),
    ),
    views=views.VIEWS,
    screens=(
        ScreenSpec(
            item_type="reminder",
            model=Reminder,
            open=render_reminder,
            label=telegram.reminder_citation_label,
        ),
    ),
    commands=(
        ScreenCommand(
            handler=render_reminders,
            command="reminders",
            description="Your Reminders",
            nav="reminders",
            title="⏰ Reminders",
        ),
    ),
    callback_actions=REMINDER_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
