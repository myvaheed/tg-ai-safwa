"""Memory in the background: reading the file, and the upkeep that rewrites it.

Both tasks are here rather than beside the operations they call: a task that never
returns belongs to the lifecycle.
"""

from __future__ import annotations

from tg_agent_shell.foundation.poll import run_poll
from tg_agent_shell.telegram.manifest import BackgroundContext, BackgroundTask

from .api import memory_store, memory_upkeep
from .use_cases import run_due_memory_maintenance

MEMORY_MAINTENANCE_INTERVAL_SECONDS = 60.0


async def _poll_memory_file(context: BackgroundContext) -> None:
    memory = memory_store(context.services)
    await run_poll(
        memory.sync, poll_seconds=memory.poll_seconds, name="Reading memory.md"
    )


async def _maintain_memory(context: BackgroundContext) -> None:
    async def maintain() -> None:
        await run_due_memory_maintenance(
            memory_upkeep(context.services),
            context.sessions,
            context.owner_id,
            context.timezone,
            run_background=context.services.turn.run_background,
        )

    await run_poll(
        maintain,
        poll_seconds=MEMORY_MAINTENANCE_INTERVAL_SECONDS,
        name="Memory upkeep",
    )


MEMORY_FILE_POLL = BackgroundTask("memory-file-poll", _poll_memory_file)
MEMORY_MAINTENANCE = BackgroundTask("memory-maintenance", _maintain_memory)
