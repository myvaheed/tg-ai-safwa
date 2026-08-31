"""Reminders: a trigger the owner set, fired by schedule arithmetic alone.

The poll writes a Cue and nothing else, so it needs no adapter at all: what a Reminder says
reaches the owner through the Cue queue, like everything else Safwa says first.
"""

from __future__ import annotations

from ...bootstrap.module_manifest import (
    BackgroundContext,
    BackgroundTask,
    FeatureModule,
    ProposalContribution,
)
from ...foundation.screens import ScreenCommand
from ...telegram.callbacks import REMINDER_CALLBACK_ACTIONS
from ...telegram.commands import command_reminders
from . import agent, proposal, telegram, views
from .background import run_scheduler
from .use_cases import reconcile_reminders


async def _poll_due_reminders(context: BackgroundContext) -> None:
    if not context.settings.scheduler_enabled:
        return
    await run_scheduler(
        context.sessions,
        timezone=context.settings.timezone,
        poll_seconds=context.settings.scheduler_poll_seconds,
    )


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
    background=(BackgroundTask("reminder-scheduler", _poll_due_reminders),),
    commands=(
        ScreenCommand(
            handler=command_reminders,
            command="reminders",
            description="Your Reminders",
            nav="reminders",
        ),
    ),
    callback_actions=REMINDER_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
