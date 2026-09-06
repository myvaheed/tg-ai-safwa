"""What another feature may ask of Memory."""

from __future__ import annotations

from tg_agent_shell.telegram import Services

from .store import MemoryFileStore
from .upkeep import MemoryUpkeep


def memory_store(services: Services) -> MemoryFileStore:
    """The one reader of `memory.md`, off the bag the composition root filled."""
    return services.features.memory


def memory_upkeep(services: Services) -> MemoryUpkeep:
    """The one writer of `memory.md`, off the bag the composition root filled."""
    return services.features.memory_upkeep


async def memory_health(memory: MemoryFileStore) -> str:
    """Whether `memory.md` can be read at all, in the one line the owner is shown."""
    snapshot = await memory.sync()
    return snapshot.error or "OK"
