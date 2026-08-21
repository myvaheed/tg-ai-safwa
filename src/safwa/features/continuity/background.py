"""Persona continuity in the background: the memory file, and the memory it maintains."""

from __future__ import annotations

from ...bootstrap.module_manifest import BackgroundContext, BackgroundTask
from .api import run_memory_maintenance


async def _poll_memory_file(context: BackgroundContext) -> None:
    await context.memory.poll()


async def _maintain_memory(context: BackgroundContext) -> None:
    await run_memory_maintenance(
        context.continuity,
        context.sessions,
        context.settings.telegram_owner_id,
        context.settings.timezone,
        run_background=context.services.guard.run_background,
    )


MEMORY_FILE_POLL = BackgroundTask("memory-file-poll", _poll_memory_file)
MEMORY_MAINTENANCE = BackgroundTask("memory-maintenance", _maintain_memory)
