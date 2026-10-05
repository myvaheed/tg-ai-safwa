"""The two loops an application with hooks runs: the Cue poll and the hook tick poll.

They belong to no feature, and a feature has no loop of its own: its work on a timer is a hook
on a tick. The composition root starts them and cancels them with the polling loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..hooks.contracts import Tick
from ..telegram import owner_anchor
from ..telegram.services import Services
from .background import run_cue_queue
from .initiatives import run_ticks
from .runtime import CueRuntime, tick_chat


@dataclass(frozen=True, slots=True)
class BackgroundContext:
    """What a loop is given once the application is built."""

    owner_id: int
    timezone: str
    scheduler_enabled: bool
    poll_seconds: float
    sessions: async_sessionmaker[AsyncSession]
    bot: Bot
    services: Services


@dataclass(frozen=True, slots=True)
class BackgroundTask:
    name: str
    run: Callable[[BackgroundContext], Awaitable[None]]


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
        delivered=runtime.delivered,
        release=runtime.release,
        expire=runtime.expire_review,
        prepare=runtime.prepare,
        passing=context.services.hooks.passing,
        poll_seconds=context.poll_seconds,
    )


async def _tick_hooks(context: BackgroundContext) -> None:
    if not context.scheduler_enabled or not context.services.hooks.listens(Tick):
        return
    await run_ticks(
        context.services.hooks,
        context.sessions,
        resources=context.services.features,
        timezone=context.timezone,
        poll_seconds=context.poll_seconds,
        chat=tick_chat(context.services, owner_anchor(context.bot, context.owner_id)),
    )


CUE_QUEUE = BackgroundTask("cue-queue", _poll_cues)
HOOK_TICKS = BackgroundTask("hook-ticks", _tick_hooks)
BACKGROUND_TASKS = (CUE_QUEUE, HOOK_TICKS)
