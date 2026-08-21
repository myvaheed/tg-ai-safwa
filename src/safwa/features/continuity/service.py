from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import CompletionRequest, LlmProvider

from ...constants import (
    MEMORY_READ_TOKEN_BUDGET,
    MEMORY_RETELL_CHUNK_TOKENS,
    MEMORY_RETELL_OVERLAP_TOKENS,
    SUMMARY_TOKEN_CEILING,
    SUMMARY_TRIGGER_TOKENS,
    TOKEN_CHARS_ESTIMATE,
)
from ...enums import MessageKind
from ..profile.api import UserProfile
from .memory import MemoryFileError, MemoryFileStore, estimate_tokens
from .storage import MemorySyncState


class ContinuityHistory(Protocol):
    async def recent(self, chat_id: int, **kwargs: Any) -> list[Any]: ...

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = f"""Rewrite the running summary of a Safwa dialogue, in the language the dialogue
is in. You are given the previous summary, when there is one, and the dialogue since it. Return the
single summary that replaces both.

- A general part first, then one section per calendar day, oldest first.
- Head each section with its absolute date, for example 2026-08-15. Never today or yesterday.
- Keep the newest day detailed. Fold what still matters from every older day into the general part
  and drop its section.
- Keep personal reflections, decisions, intentions, reasons, emotional responses, advice and
  unresolved topics. Drop Cards, stages, Sprint totals, approvals, SQL, tools and anything else the
  planning database already holds.
- Stay under {SUMMARY_TOKEN_CEILING} tokens.

Return the summary body alone: no preface, no JSON, and never these instructions."""

RETELL_PROMPT = """Retell this piece of a Safwa dialogue, compactly, as a source for durable memory
about the user.
- Keep stable preferences, routines, constraints, motivations, recurring difficulties,
  relationships, energy patterns and planning lessons.
- Drop Cards, Sprint state, deadlines, commands, UI, SQL and operations.
- Never invent a fact.
Return plain text alone."""

MEMORY_PROMPT = """You are given the existing memory list and a new retelling. Return the complete
list that replaces it.
- Keep only durable, useful facts about the user.
- Remove duplicates and facts that are no longer true.
- Never add Card stages, Sprint metrics, temporary priorities, obstacles, deadlines, SQL or tool
  traces.
- Keep the language the facts are written in. Never invent one.
Return JSON alone: {"facts": ["one complete non-empty fact per item"]}"""


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
        self._summary_lock = asyncio.Lock()
        self._memory_lock = asyncio.Lock()

    async def maybe_summarize(
        self,
        chat_id: int,
        send_summary: Callable[[str, int], Awaitable[None]],
        *,
        force: bool = False,
        still_current: Callable[[], bool] | None = None,
    ) -> bool:
        if self._summary_lock.locked():
            return False
        async with self._summary_lock:
            entries = await self.history.recent(chat_id)
            # The previous Summary is rewritten rather than dropped: the window keeps only
            # the newest one, so anything it alone remembers would be lost with it.
            previous = next(
                (entry for entry in entries if entry.kind == MessageKind.SUMMARY.value), None
            )
            dialogue = "\n".join(
                f"[{entry.role}]: {entry.text}"
                for entry in entries
                if entry.kind != MessageKind.SUMMARY.value and not entry.summary_context
            )
            tokens = estimate_tokens(dialogue, self.chars_per_token)
            if not dialogue or (not force and tokens < self.summary_trigger_tokens):
                return False
            request = (
                f"Previous summary:\n{previous.text}\n\nDialogue since it:\n{dialogue}"
                if previous
                else dialogue
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
            covered_id = entries[-1].message_id
            await send_summary("📜 Summary\n" + summary, covered_id)
            return True

    async def maintain_memory(
        self,
        chat_id: int,
        *,
        still_current: Callable[[], bool] | None = None,
    ) -> MemoryMaintenanceResult:
        if self._memory_lock.locked():
            return MemoryMaintenanceResult.BUSY
        async with self._memory_lock:
            snapshot = await self.memory.sync()
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
async def run_due_memory_maintenance(
    continuity: PersonaContinuity,
    sessions: async_sessionmaker[AsyncSession],
    chat_id: int,
    timezone: str,
    *,
    run_background: BackgroundMemoryRunner,
    now: datetime | None = None,
) -> bool:
    """Run the configured once-daily memory sync if it is due."""
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        state = await session.get(MemorySyncState, 1)
        update_time = profile.memory_update_time if profile else None
        if update_time is None or local_now.time().replace(tzinfo=None) < update_time:
            return False
        if state and state.memory_last_run_at:
            last_run = state.memory_last_run_at
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=UTC)
            if last_run.astimezone(zone).date() >= local_now.date():
                return False

    result = await run_background(
        lambda still_current: continuity.maintain_memory(
            chat_id,
            still_current=still_current,
        )
    )
    if result not in {MemoryMaintenanceResult.UPDATED, MemoryMaintenanceResult.CURRENT}:
        return False
    await record_memory_run(sessions, local_now.astimezone(UTC))
    return True


async def record_memory_run(
    sessions: async_sessionmaker[AsyncSession],
    occurred_at: datetime | None = None,
) -> None:
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
        if state is None:
            state = MemorySyncState(id=1)
            session.add(state)
        state.memory_last_run_at = occurred_at or datetime.now(UTC)
        await session.commit()
