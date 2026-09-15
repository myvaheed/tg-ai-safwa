"""Binding the Cue poll to this application.

Cues are not a feature — no business rule is written here — so this task is started
alongside the feature tasks rather than through a `FeatureModule` of its own.
"""

from __future__ import annotations

from ..telegram.manifest import BackgroundContext, BackgroundTask
from .background import run_cue_queue
from .runtime import CueRuntime


async def _poll_cues(context: BackgroundContext) -> None:
    if not context.scheduler_enabled:
        return
    runtime = CueRuntime(
        context.services, context.bot, owner_id=context.owner_id
    )
    await run_cue_queue(
        context.sessions,
        gate=runtime.can_speak,
        speak=runtime.speak,
        release=runtime.release,
        expire=runtime.expire_review,
        prepare=runtime.prepare,
        poll_seconds=context.poll_seconds,
    )


CUE_QUEUE = BackgroundTask("cue-queue", _poll_cues)
