from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .ai.provider import OpenAICompatibleProvider
from .constants import (
    HISTORY_CONTINUITY_LIMIT,
    MEMORY_MAINTENANCE_INTERVAL_SECONDS,
    MEMORY_RETELL_CHUNK_TOKENS,
    MEMORY_RETELL_OVERLAP_TOKENS,
    SUMMARY_TRIGGER_TOKENS,
    TOKEN_CHARS_ESTIMATE,
)
from .history import HistoryBoundaryMissing, TelegramHistorySource
from .memory import MemoryFileError, MemoryFileStore, estimate_tokens
from .models import MemorySyncState, UserProfile

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """Summarize the supplied canonical Safwa persona dialogue in its natural language.
Preserve personal reflections, decisions, intentions, reasons, emotional responses, advice, and unresolved
topics. Omit current card inventories, stages, Sprint totals, approvals, SQL, tools, and other operational
details because the planning database is authoritative for those. Do not summarize these instructions.
Return only the concise summary body, with no JSON or preface."""

RETELL_PROMPT = """Retell this canonical Telegram dialogue chunk as a compact source for durable personal
memory. Preserve stable preferences, routines, constraints, motivations, recurring difficulties,
relationships, energy patterns, and planning lessons. Omit transient cards, Sprint state, deadlines,
commands, UI, SQL, and operations. Do not invent facts. Return plain text only."""

MEMORY_PROMPT = """Reconcile the retelling into the complete persistent memory list. Keep only durable,
useful personal facts. Remove duplicates and obsolete facts. Never add card stages, Sprint metrics,
temporary priorities, obstacles, deadlines, SQL, or tool traces. Return JSON only as
{"facts":["one complete non-empty fact per item"]}. Keep the existing language and do not invent facts."""


class MemoryMaintenanceResult(StrEnum):
    UPDATED = "updated"
    CURRENT = "current"
    BUSY = "busy"
    INVALID = "invalid"
    BOUNDARY_MISSING = "boundary_missing"


class PersonaContinuity:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        history: TelegramHistorySource,
        provider: OpenAICompatibleProvider,
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
    ) -> bool:
        if self._summary_lock.locked():
            return False
        async with self._summary_lock:
            entries = await self.history.recent(chat_id, limit=HISTORY_CONTINUITY_LIMIT)
            dialogue = "\n".join(
                f"[{entry.role}]: {entry.text}"
                for entry in entries
                if entry.kind not in {"summary", "session_start"} and not entry.summary_context
            )
            tokens = estimate_tokens(dialogue, self.chars_per_token)
            if tokens < self.summary_trigger_tokens or not entries:
                return False
            summary = await self.provider.complete(
                [
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": dialogue},
                ],
                temperature=0.1,
            )
            covered_id = entries[-1].message_id
            await send_summary("📜 Summary\n" + summary, covered_id)
            return True

    async def maintain_memory(self, chat_id: int) -> MemoryMaintenanceResult:
        if self._memory_lock.locked():
            return MemoryMaintenanceResult.BUSY
        async with self._memory_lock:
            snapshot = await self.memory.sync()
            if not snapshot.valid:
                return MemoryMaintenanceResult.INVALID
            async with self.sessions() as session:
                state = await session.get(MemorySyncState, 1)
                processed_id = state.processed_message_id if state else None
            try:
                entries = await self.history.recent(
                    chat_id, limit=HISTORY_CONTINUITY_LIMIT, require_boundary=True
                )
            except HistoryBoundaryMissing:
                return MemoryMaintenanceResult.BOUNDARY_MISSING
            new_entries = [
                entry for entry in entries if not processed_id or entry.message_id > processed_id
            ]
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
                retelling = await self.provider.complete(
                    [
                        {"role": "system", "content": RETELL_PROMPT},
                        {"role": "user", "content": chunk},
                    ],
                    temperature=0.1,
                )
                raw_result = await self.provider.complete(
                    [
                        {"role": "system", "content": MEMORY_PROMPT},
                        {
                            "role": "user",
                            "content": "Existing memory:\n"
                            + "\n".join(facts)
                            + "\n\nNew retelling:\n"
                            + retelling,
                        },
                    ],
                    temperature=0,
                )
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
                    logger.warning("Ignoring invalid automatic memory response")
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
                state.processed_message_id = max(entry.message_id for entry in new_entries)
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


async def run_memory_maintenance(
    continuity: PersonaContinuity,
    sessions: async_sessionmaker[AsyncSession],
    chat_id: int,
    is_foreground_busy: Callable[[], bool],
    timezone: str,
    *,
    interval_seconds: float = MEMORY_MAINTENANCE_INTERVAL_SECONDS,
) -> None:
    while True:
        try:
            await run_due_memory_maintenance(
                continuity,
                sessions,
                chat_id,
                is_foreground_busy,
                timezone,
            )
        except Exception:
            logger.exception("Scheduled memory synchronization failed")
        await asyncio.sleep(interval_seconds)


async def run_due_memory_maintenance(
    continuity: PersonaContinuity,
    sessions: async_sessionmaker[AsyncSession],
    chat_id: int,
    is_foreground_busy: Callable[[], bool],
    timezone: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Run the configured once-daily memory sync if it is due."""
    if is_foreground_busy():
        return False
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

    result = await continuity.maintain_memory(chat_id)
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


def parse_memory_update_time(value: str) -> time | None:
    normalized = value.strip().lower()
    if normalized == "off":
        return None
    if len(normalized) != 5 or normalized[2] != ":":
        raise ValueError("Memory update time must use HH:MM, for example 03:00, or off.")
    try:
        return time.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(
            "Memory update time must use a valid 24-hour HH:MM value, for example 03:00, or off."
        ) from error
