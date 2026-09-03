"""What another feature may ask of Continuity."""

from __future__ import annotations

from .memory import MemoryFileStore


async def memory_health(memory: MemoryFileStore) -> str:
    """Whether `memory.md` can be read at all, in the one line the owner is shown."""
    snapshot = await memory.sync()
    return snapshot.error or "OK"
