"""The object that turns the dialogue into a Summary and into durable memory.

One long-lived collaborator, built once by the composition root and handed to the
adapters. It reads the canonical conversation, calls the provider, and hands the result
to the operations in `use_cases.py`; it owns no schedule and no state of its own.

It also owns no lock. `TurnManager.run_background` is the single lease every caller
takes, and it already refuses a second background run — a lock here would be a second
mechanism for the property the turn exists to hold.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import CompletionRequest, LlmProvider
from tg_agent_shell.foundation.kinds import MessageKind

from ...constants import SUMMARY_TRIGGER_TOKENS
from ...foundation.tokens import TOKEN_CHARS_ESTIMATE, estimate_tokens
from .agent import MEMORY_PROMPT, RETELL_PROMPT, SUMMARY_PROMPT
from .memory import MemoryFileError, MemoryFileStore
from .model import SUMMARY_HEADER, MemorySyncState

logger = logging.getLogger(__name__)

MEMORY_RETELL_CHUNK_TOKENS = 2_000
MEMORY_RETELL_OVERLAP_TOKENS = 500
# Memory reads back to its own cursor rather than to a fixed message count; this only caps
# how much one catch-up run may swallow after a long gap.
MEMORY_READ_TOKEN_BUDGET = 20_000


class ContinuityHistory(Protocol):
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


class PersonaContinuity:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        history: ContinuityHistory,
        provider: LlmProvider,
        memory: MemoryFileStore,
        *,
        summary_trigger_tokens: int = SUMMARY_TRIGGER_TOKENS,
        chars_per_token: float = TOKEN_CHARS_ESTIMATE,
    ) -> None:
        self.sessions = sessions
        self.history = history
        self.provider = provider
        self.memory = memory
        self.summary_trigger_tokens = summary_trigger_tokens
        self.chars_per_token = chars_per_token

    async def close_window(
        self,
        chat_id: int,
        write: Callable[[str], Awaitable[None]],
        *,
        force: bool = False,
        still_current: Callable[[], bool] | None = None,
    ) -> bool:
        """Write a Summary if the dialogue has outgrown the budget, and say whether it did."""
        entries = await self.history.recent(chat_id)
        # The previous Summary is rewritten rather than dropped: the window keeps only
        # the newest one, so anything it alone remembers would be lost with it.
        previous = next(
            (entry for entry in entries if entry.kind == MessageKind.SUMMARY.value), None
        )
        dialogue = "\n".join(
            f"[{entry.role}]: {entry.text}"
            for entry in entries
            if entry.kind != MessageKind.SUMMARY.value and not entry.before_edge
        )
        tokens = estimate_tokens(dialogue, self.chars_per_token)
        if not dialogue or (not force and tokens < self.summary_trigger_tokens):
            return False
        # The window already labelled the previous Summary as what it is; saying so a
        # second time is one more line for a small model to reconcile.
        request = (
            f"{previous.text}\n\nDialogue since it:\n{dialogue}" if previous else dialogue
        )
        summary = (
            await self.provider.complete(
                CompletionRequest(
                    messages=(
                        {"role": "system", "content": SUMMARY_PROMPT},
                        {"role": "user", "content": request},
                    ),
                    temperature=0.1,
                )
            )
        ).content
        if still_current is not None and not still_current():
            return False
        current_entries = await self.history.recent(chat_id)
        snapshot = [(entry.message_id, entry.kind, entry.text) for entry in entries]
        current = [(entry.message_id, entry.kind, entry.text) for entry in current_entries]
        if current != snapshot:
            logger.info("Discarding a stale automatic summary")
            return False
        await write(f"{SUMMARY_HEADER}\n{summary}")
        return True

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
