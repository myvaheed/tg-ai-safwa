"""Reminders: a trigger the owner set, fired by schedule arithmetic alone.

The poll takes its gate and its escalation as callables, so `background.py` never reaches
for a Telegram adapter. Binding the two is wiring, and wiring lives here.
"""

from __future__ import annotations

from ...bootstrap.module_manifest import (
    BackgroundContext,
    BackgroundTask,
    FeatureModule,
    ProposalContribution,
)
from . import agent, proposal, telegram, views
from .background import run_scheduler
from .use_cases import reconcile_reminders


async def _poll_due_reminders(context: BackgroundContext) -> None:
    if not context.settings.scheduler_enabled:
        return
    runtime = telegram.ReminderRuntime(
        context.services,
        context.bot,
        owner_id=context.settings.telegram_owner_id,
        timezone=context.settings.timezone,
    )
    await run_scheduler(
        context.sessions,
        timezone=context.settings.timezone,
        gate=runtime.can_escalate,
        still_current=runtime.still_current,
        release=runtime.release,
        escalate=runtime.escalate,
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
)
