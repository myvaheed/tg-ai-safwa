from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .constants import MEMORY_POLL_SECONDS, MEMORY_TOKEN_BUDGET, TOKEN_CHARS_ESTIMATE
from .models import MemoryFactCache, MemorySyncState


class MemoryFileError(ValueError):
    pass


@dataclass(frozen=True)
class MemorySnapshot:
    facts: tuple[str, ...]
    file_hash: str
    estimated_tokens: int
    valid: bool = True
    error: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.facts) if self.valid else ""


def estimate_tokens(text: str, chars_per_token: float = TOKEN_CHARS_ESTIMATE) -> int:
    return int((len(text) / max(chars_per_token, 1.0)) + 0.999)


def parse_memory(content: str) -> tuple[str, ...]:
    if not content:
        return ()
    raw_lines = content.splitlines()
    if any(not line.strip() for line in raw_lines):
        raise MemoryFileError("memory.md must contain one non-empty fact per line")
    return tuple(line.strip() for line in raw_lines)


def memory_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class MemoryFileStore:
    """Authoritative file-backed memory with a disposable SQLite mirror."""

    def __init__(
        self,
        path: Path,
        sessions: async_sessionmaker[AsyncSession],
        *,
        token_budget: int = MEMORY_TOKEN_BUDGET,
        chars_per_token: float = TOKEN_CHARS_ESTIMATE,
        poll_seconds: float = MEMORY_POLL_SECONDS,
    ) -> None:
        self.path = path
        self.sessions = sessions
        self.token_budget = token_budget
        self.chars_per_token = chars_per_token
        self.poll_seconds = poll_seconds
        self._lock = asyncio.Lock()

    def _read(self) -> tuple[bytes, float | None]:
        if not self.path.exists():
            return b"", None
        return self.path.read_bytes(), self.path.stat().st_mtime

    async def sync(self) -> MemorySnapshot:
        async with self._lock:
            return await self._sync_locked()

    async def _sync_locked(self) -> MemorySnapshot:
        raw, mtime = self._read()
        digest = memory_hash(raw)
        try:
            content = raw.decode("utf-8")
            facts = parse_memory(content)
            tokens = estimate_tokens(content, self.chars_per_token)
            if tokens > self.token_budget:
                raise MemoryFileError(
                    f"memory.md is about {tokens} tokens; configured limit is {self.token_budget}"
                )
        except (UnicodeDecodeError, MemoryFileError) as error:
            async with self.sessions() as session:
                state = await session.get(MemorySyncState, 1)
                if state is None:
                    state = MemorySyncState(id=1)
                    session.add(state)
                state.error = str(error)
                state.file_hash = digest
                state.file_mtime = mtime
                await session.commit()
            return MemorySnapshot((), digest, 0, valid=False, error=str(error))

        async with self.sessions() as session:
            state = await session.get(MemorySyncState, 1)
            if state is None:
                state = MemorySyncState(id=1)
                session.add(state)
            current = list(
                await session.scalars(select(MemoryFactCache).order_by(MemoryFactCache.line_number))
            )
            for index, fact in enumerate(facts, start=1):
                if index <= len(current):
                    row = current[index - 1]
                    if row.fact != fact:
                        row.fact = fact
                        row.provenance = "manual"
                else:
                    session.add(MemoryFactCache(line_number=index, fact=fact, provenance="manual"))
            if len(current) > len(facts):
                await session.execute(
                    delete(MemoryFactCache).where(MemoryFactCache.line_number > len(facts))
                )
            state.file_hash = digest
            state.file_mtime = mtime
            state.error = None
            await session.commit()
        return MemorySnapshot(facts, digest, estimate_tokens(content, self.chars_per_token))

    async def replace_facts(
        self,
        facts: list[str],
        *,
        expected_hash: str,
        provenance: str = "inferred",
    ) -> MemorySnapshot:
        normalized = [fact.strip() for fact in facts]
        if any(not fact for fact in normalized):
            raise MemoryFileError("Memory facts cannot be empty")
        content = "\n".join(normalized)
        if content:
            content += "\n"
        tokens = estimate_tokens(content, self.chars_per_token)
        if tokens > self.token_budget:
            raise MemoryFileError(
                f"Automatic memory update exceeds the {self.token_budget}-token budget"
            )
        async with self._lock:
            raw, _ = self._read()
            if memory_hash(raw) != expected_hash:
                await self._sync_locked()
                raise MemoryFileError("memory.md changed during update; retry with the latest file")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".md.tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            raw_after, _ = self._read()
            if memory_hash(raw_after) != expected_hash:
                temporary.unlink(missing_ok=True)
                await self._sync_locked()
                raise MemoryFileError("memory.md changed during update; local edit was preserved")
            os.replace(temporary, self.path)
            snapshot = await self._sync_locked()
            async with self.sessions() as session:
                rows = await session.scalars(select(MemoryFactCache))
                for row in rows:
                    row.provenance = provenance
                await session.commit()
            return snapshot

    async def append_manual(self, fact: str) -> MemorySnapshot:
        snapshot = await self.sync()
        if not snapshot.valid:
            raise MemoryFileError(snapshot.error or "memory.md is invalid")
        return await self.replace_facts(
            [*snapshot.facts, fact.strip()], expected_hash=snapshot.file_hash, provenance="manual"
        )

    async def forget_line(self, line_number: int) -> MemorySnapshot:
        snapshot = await self.sync()
        if not snapshot.valid:
            raise MemoryFileError(snapshot.error or "memory.md is invalid")
        if line_number < 1 or line_number > len(snapshot.facts):
            raise MemoryFileError("Memory line does not exist")
        facts = list(snapshot.facts)
        facts.pop(line_number - 1)
        return await self.replace_facts(
            facts, expected_hash=snapshot.file_hash, provenance="manual"
        )

    async def poll(self, on_error=None) -> None:  # type: ignore[no-untyped-def]
        last_hash: str | None = None
        while True:
            snapshot = await self.sync()
            if snapshot.file_hash != last_hash and not snapshot.valid and on_error:
                await on_error(snapshot.error or "Invalid memory.md")
            last_hash = snapshot.file_hash
            await asyncio.sleep(self.poll_seconds)
