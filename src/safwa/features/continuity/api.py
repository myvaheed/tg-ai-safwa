"""Public Continuity operations and data used by the application shell."""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...constants import MEMORY_MAINTENANCE_INTERVAL_SECONDS
from .memory import (
    MemoryFileError,
    MemoryFileStore,
    MemorySnapshot,
    estimate_tokens,
    memory_hash,
    parse_memory,
)
from .service import (
    BackgroundMemoryRunner,
    MemoryMaintenanceResult,
    PersonaContinuity,
    record_memory_run,
    run_due_memory_maintenance,
)
from .storage import MemoryFactCache, MemorySyncState, SummaryState

logger = logging.getLogger(__name__)


async def run_memory_maintenance(
    continuity: PersonaContinuity,
    sessions: async_sessionmaker[AsyncSession],
    chat_id: int,
    timezone: str,
    *,
    run_background: BackgroundMemoryRunner,
    interval_seconds: float = MEMORY_MAINTENANCE_INTERVAL_SECONDS,
) -> None:
    """Keep checking the once-daily memory maintenance eligibility."""
    while True:
        try:
            await run_due_memory_maintenance(
                continuity,
                sessions,
                chat_id,
                timezone,
                run_background=run_background,
            )
        except Exception:
            logger.exception("Scheduled memory synchronization failed")
        await asyncio.sleep(interval_seconds)

__all__ = [
    "MemoryFactCache",
    "MemoryFileError",
    "MemoryFileStore",
    "MemoryMaintenanceResult",
    "MemorySnapshot",
    "MemorySyncState",
    "PersonaContinuity",
    "SummaryState",
    "estimate_tokens",
    "memory_hash",
    "parse_memory",
    "record_memory_run",
    "run_due_memory_maintenance",
    "run_memory_maintenance",
]
