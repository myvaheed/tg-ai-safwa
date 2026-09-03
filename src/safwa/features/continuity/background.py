"""Persona continuity in the background: the memory file, and the memory it maintains.

Both loops are here rather than beside the operations they call: a task that never
returns belongs to the lifecycle, and a task that dies on one bad turn stops the feature
for the rest of the process, so each iteration catches and logs instead.
"""

from __future__ import annotations

import asyncio
import logging

from ...constants import MEMORY_MAINTENANCE_INTERVAL_SECONDS
from ...shell.manifest import BackgroundContext, BackgroundTask
from .use_cases import run_due_memory_maintenance

logger = logging.getLogger(__name__)


async def _poll_memory_file(context: BackgroundContext) -> None:
    memory = context.services.memory
    while True:
        try:
            await memory.sync()
        except Exception:
            logger.exception("Reading memory.md failed")
        await asyncio.sleep(memory.poll_seconds)


async def _maintain_memory(context: BackgroundContext) -> None:
    while True:
        try:
            await run_due_memory_maintenance(
                context.services.continuity,
                context.sessions,
                context.owner_id,
                context.timezone,
                run_background=context.services.turn.run_background,
            )
        except Exception:
            logger.exception("Scheduled memory synchronization failed")
        await asyncio.sleep(MEMORY_MAINTENANCE_INTERVAL_SECONDS)


MEMORY_FILE_POLL = BackgroundTask("memory-file-poll", _poll_memory_file)
MEMORY_MAINTENANCE = BackgroundTask("memory-maintenance", _maintain_memory)
