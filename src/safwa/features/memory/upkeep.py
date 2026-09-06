"""The object that turns the dialogue into durable memory.

One long-lived collaborator, built once by the composition root and reached from `api.py`.
It reads the canonical conversation, calls the provider, and hands the result to the
operations in `use_cases.py`; it owns no schedule and no state of its own.

It also owns no lock. `TurnManager.run_background` is the single lease every caller takes,
and it already refuses a second background run — a lock here would be a second mechanism
for the property the turn exists to hold.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import CompletionRequest, LlmProvider

from ...foundation.tokens import TOKEN_CHARS_ESTIMATE
from .agent import MEMORY_PROMPT, RETELL_PROMPT
from .model import MemorySyncState
from .store import MemoryFileError, MemoryFileStore

logger = logging.getLogger(__name__)

MEMORY_RETELL_CHUNK_TOKENS = 2_000
MEMORY_RETELL_OVERLAP_TOKENS = 500
# Memory reads back to its own cursor rather than to a fixed message count; this only caps
# how much one catch-up run may swallow after a long gap.
MEMORY_READ_TOKEN_BUDGET = 20_000


class DialogueHistory(Protocol):
    async def recent(self, chat_id: int, **kwargs: Any) -> list[Any]: ...


class MemoryMaintenanceResult(StrEnum):
    UPDATED = "updated"
    CURRENT = "current"
    BUSY = "busy"
    INVALID = "invalid"


BackgroundMemoryOperation = Callable[
    [Callable[[], bool]], Awaitable[MemoryMaintenanceResult]
]
BackgroundMemoryRunner = Callable[
    [BackgroundMemoryOperation], Awaitable[MemoryMaintenanceResult | None]
]


class MemoryUpkeep:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        history: DialogueHistory,
        provider: LlmProvider,
        memory: MemoryFileStore,
        *,
        chars_per_token: float = TOKEN_CHARS_ESTIMATE,
    ) -> None:
        self.sessions = sessions
        self.history = history
        self.provider = provider
        self.memory = memory
        self.chars_per_token = chars_per_token

    async def maintain_memory(
        self,
        chat_id: int,
        *,
        still_current: Callable[[], bool] | None = None,
    ) -> MemoryMaintenanceResult:
        snapshot = await self.memory.sync()
        if snapshot.error is not None:
            # The hash of a file we could not read still matches, so a replacement
            # would overwrite it with facts built from the nothing we managed to read.
            logger.warning("Memory maintenance skipped: %s", snapshot.error)
            return MemoryMaintenanceResult.INVALID
        if still_current is not None and not still_current():
            return MemoryMaintenanceResult.BUSY
        async with self.sessions() as session:
            state = await session.get(MemorySyncState, 1)
            cursor = state.processed_until if state else None
        # Reading back to the cursor rather than to a message count is what keeps a
        # long gap from silently falling out of the window.
        new_entries = await self.history.recent(
            chat_id, token_budget=MEMORY_READ_TOKEN_BUDGET, since=cursor
        )
        if still_current is not None and not still_current():
            return MemoryMaintenanceResult.BUSY
        if not new_entries:
            return MemoryMaintenanceResult.CURRENT
        raw = "\n".join(f"[{e.role}]: {e.text}" for e in new_entries)
        chunks = self._chunks(
            raw,
            limit_chars=int(MEMORY_RETELL_CHUNK_TOKENS * self.chars_per_token),
            overlap_chars=int(MEMORY_RETELL_OVERLAP_TOKENS * self.chars_per_token),
        )
        facts = list(snapshot.facts)
        expected_hash = snapshot.file_hash
        for chunk in chunks:
            retelling = (
                await self.provider.complete(
                    CompletionRequest(
                        messages=(
                            {"role": "system", "content": RETELL_PROMPT},
                            {"role": "user", "content": chunk},
                        ),
                        temperature=0.1,
                    )
                )
            ).content
            if still_current is not None and not still_current():
                return MemoryMaintenanceResult.BUSY
            raw_result = (
                await self.provider.complete(
                    CompletionRequest(
                        messages=(
                            {"role": "system", "content": MEMORY_PROMPT},
                            {
                                "role": "user",
                                "content": "Existing memory:\n"
                                + "\n".join(facts)
                                + "\n\nNew retelling:\n"
                                + retelling,
                            },
                        ),
                        temperature=0,
                    )
                )
            ).content
            if still_current is not None and not still_current():
                return MemoryMaintenanceResult.BUSY
            try:
                result = json.loads(
                    raw_result.removeprefix("```json").removesuffix("```").strip()
                )
                candidate = result.get("facts")
                if not isinstance(candidate, list) or not all(
                    isinstance(x, str) for x in candidate
                ):
                    raise ValueError("invalid facts")
                facts = [fact.strip() for fact in candidate if fact.strip()]
            except (json.JSONDecodeError, ValueError, AttributeError):
                logger.warning(
                    "Automatic memory response was invalid; leaving the cursor unchanged"
                )
                return MemoryMaintenanceResult.INVALID
        if still_current is not None and not still_current():
            return MemoryMaintenanceResult.BUSY
        try:
            updated = await self.memory.replace_facts(
                facts, expected_hash=expected_hash, provenance="inferred"
            )
        except MemoryFileError:
            return MemoryMaintenanceResult.INVALID
        async with self.sessions() as session:
            state = await session.get(MemorySyncState, 1)
            if state is None:
                state = MemorySyncState(id=1)
                session.add(state)
            state.processed_until = max(entry.created_at for entry in new_entries)
            state.file_hash = updated.file_hash
            await session.commit()
        return MemoryMaintenanceResult.UPDATED

    @staticmethod
    def _chunks(text: str, *, limit_chars: int, overlap_chars: int) -> list[str]:
        if len(text) <= limit_chars:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(len(text), start + limit_chars)
            chunks.append(text[start:end])
            if end == len(text):
                break
            start = max(start + 1, end - overlap_chars)
        return chunks
