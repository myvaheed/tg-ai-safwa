from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...constants import MEMORY_POLL_SECONDS, MEMORY_TOKEN_BUDGET, TOKEN_CHARS_ESTIMATE
from ...foundation.tokens import estimate_tokens
from .model import MemoryFactCache, MemorySyncState


class MemoryFileError(ValueError):
    pass


@dataclass(frozen=True)
class MemorySnapshot:
    """What one reading of `memory.md` found, and why it found nothing usable.

    `error` is not a format opinion about the owner's text: it is set only when the file
    cannot be read as text at all, or when reading it would spend more of the prompt than
    the configured budget allows.  Either way `text` is empty, so an unreadable file
    disables memory injection instead of failing the turn that needed it.
    """

    facts: tuple[str, ...]
    file_hash: str
    estimated_tokens: int
    error: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.facts) if self.error is None else ""


def parse_memory(content: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in content.splitlines() if line.strip())


def memory_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class MemoryFileStore:
    """Authoritative file-backed memory with a rebuildable SQLite read cache."""

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
        except UnicodeDecodeError:
            return await self._record_unreadable(
                digest, mtime, "memory.md is not UTF-8 text and was not read."
            )
        tokens = estimate_tokens(content, self.chars_per_token)
        if tokens > self.token_budget:
            return await self._record_unreadable(
                digest,
                mtime,
                f"memory.md is about {tokens} tokens; the configured limit is "
                f"{self.token_budget}.",
            )
        facts = parse_memory(content)

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
        return MemorySnapshot(facts, digest, tokens)

    async def _record_unreadable(
        self, digest: str, mtime: float | None, message: str
    ) -> MemorySnapshot:
        """Note why the file could not be read, and leave the last good cache alone."""
        async with self.sessions() as session:
            state = await session.get(MemorySyncState, 1)
            if state is None:
                state = MemorySyncState(id=1)
                session.add(state)
            state.file_hash = digest
            state.file_mtime = mtime
            state.error = message
            await session.commit()
        return MemorySnapshot((), digest, 0, error=message)

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
        if snapshot.error is not None:
            # Appending to a file we could not read would write the facts we do not have.
            raise MemoryFileError(snapshot.error)
        return await self.replace_facts(
            [*snapshot.facts, fact.strip()], expected_hash=snapshot.file_hash, provenance="manual"
        )
