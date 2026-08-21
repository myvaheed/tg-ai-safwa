"""The Reminder scheduler: schedule arithmetic only, one poll at a time."""

from __future__ import annotations

from ...bootstrap.module_manifest import BackgroundContext, BackgroundTask
from ...scheduler import run_scheduler
from ...telegram import ReminderRuntime


async def _poll_due_reminders(context: BackgroundContext) -> None:
    if not context.settings.scheduler_enabled:
        return
    runtime = ReminderRuntime(
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


REMINDER_SCHEDULER = BackgroundTask("reminder-scheduler", _poll_due_reminders)
