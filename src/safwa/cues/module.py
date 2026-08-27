"""Binding the Cue poll to this application.

Cues are not a feature — no business rule is written here — so this task is started
alongside the feature tasks rather than through a `FeatureModule` of its own.
"""

from __future__ import annotations

from ..bootstrap.module_manifest import BackgroundContext, BackgroundTask
from .background import run_cue_queue
from .runtime import CueRuntime


async def _poll_cues(context: BackgroundContext) -> None:
    if not context.settings.scheduler_enabled:
        return
    runtime = CueRuntime(
        context.services, context.bot, owner_id=context.settings.telegram_owner_id
    )
    await run_cue_queue(
        context.sessions,
        gate=runtime.can_speak,
        speak=runtime.speak,
        release=runtime.release,
        poll_seconds=context.settings.scheduler_poll_seconds,
    )


CUE_QUEUE = BackgroundTask("cue-queue", _poll_cues)
