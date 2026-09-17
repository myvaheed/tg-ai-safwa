"""Binding the Cue poll and the hook tick poll to this application.

Cues are not a feature — no business rule is written here — so these tasks are started
alongside the feature tasks rather than through a `FeatureModule` of their own.
"""

from __future__ import annotations

from ..hooks.contracts import Tick
from ..telegram.manifest import BackgroundContext, BackgroundTask
from .background import run_cue_queue
from .initiatives import run_ticks
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


async def _tick_hooks(context: BackgroundContext) -> None:
    if not context.scheduler_enabled or not context.services.hooks.listens(Tick):
        return
    await run_ticks(
        context.services.hooks,
        context.sessions,
        timezone=context.timezone,
        poll_seconds=context.poll_seconds,
    )


CUE_QUEUE = BackgroundTask("cue-queue", _poll_cues)
HOOK_TICKS = BackgroundTask("hook-ticks", _tick_hooks)
