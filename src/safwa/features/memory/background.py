"""Memory in the background: the poll that takes an analysed Sprint into what is remembered.

It is here rather than beside the operation it calls: a task that never returns belongs
to the lifecycle. The poll is the whole of the reliability — an analysis stays owed until
the poll has written it, so nothing that ends one attempt is lost.
"""

from __future__ import annotations

from tg_agent_shell.foundation.poll import run_poll
from tg_agent_shell.telegram.manifest import BackgroundContext, BackgroundTask

from .api import memory_reviewer
from .use_cases import absorb_due

MEMORY_RETRO_INTERVAL_SECONDS = 60.0


async def _absorb_analyses(context: BackgroundContext) -> None:
    async def tick() -> None:
        await absorb_due(
            memory_reviewer(context.services),
            context.sessions,
            run_background=context.services.turn.run_background,
        )

    await run_poll(tick, poll_seconds=MEMORY_RETRO_INTERVAL_SECONDS, name="Memory from the retro")


MEMORY_RETRO = BackgroundTask("memory-retro", _absorb_analyses)
