"""Reminders: a trigger the owner set, fired by schedule arithmetic alone.

The poll writes a Cue and nothing else, so it needs no adapter at all: what a Reminder says
reaches the owner through the Cue queue, like everything else Safwa says first.
"""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.proposals.api import SimilarItems
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import (
    BackgroundContext,
    BackgroundTask,
    FeatureModule,
    ProposalContribution,
)

from . import agent, proposal, telegram, views
from .background import run_scheduler
from .model import Reminder
from .telegram import REMINDER_CALLBACK_ACTIONS, render_reminder, render_reminders
from .use_cases import reconcile_reminders


async def _poll_due_reminders(context: BackgroundContext) -> None:
    if not context.scheduler_enabled:
        return
    await run_scheduler(
        context.sessions,
        timezone=context.timezone,
        poll_seconds=context.poll_seconds,
    )


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
    recover=reconcile_reminders,
    background=(BackgroundTask("reminder-scheduler", _poll_due_reminders),),
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
