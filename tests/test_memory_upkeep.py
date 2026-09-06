from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from llm_gateway import CompletionRequest, CompletionTurn
from safwa.features.memory.model import MemorySyncState
from safwa.features.memory.store import MemoryFileStore
from safwa.features.memory.upkeep import (
    MEMORY_READ_TOKEN_BUDGET,
    MemoryMaintenanceResult,
    MemoryUpkeep,
)
from safwa.features.memory.use_cases import run_due_memory_maintenance
from safwa.features.profile.model import UserProfile
from telegram_llm import HistoryEntry
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.turn import TurnManager

NOT_TEXT = b"\xff\xfe not text at all"
REPLACEMENT_FACTS = '{"facts":["A durable new fact"]}'


class StubUpkeep:
    def __init__(self, result: MemoryMaintenanceResult = MemoryMaintenanceResult.UPDATED) -> None:
        self.result = result
        self.calls: list[int] = []

    async def maintain_memory(
        self, chat_id: int, *, still_current=None
    ) -> MemoryMaintenanceResult:
        self.calls.append(chat_id)
        return self.result


class SequenceProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)

    async def complete(self, _request: CompletionRequest) -> CompletionTurn:
        return CompletionTurn(self.responses.pop(0))

    async def aclose(self) -> None:
        return None


class RecordingProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.requests.append(request)
        return CompletionTurn(self.responses.pop(0))

    async def aclose(self) -> None:
        return None


class FileEditingProvider:
    """Simulate an owner file edit while the external provider is generating."""

    def __init__(self, memory_path: Path) -> None:
        self.memory_path = memory_path
        self.calls = 0

    async def complete(self, _request: CompletionRequest) -> CompletionTurn:
        self.calls += 1
        if self.calls == 1:
            return CompletionTurn("Retold fact")
        self.memory_path.write_text("Owner edit during generation.\n", encoding="utf-8")
        return CompletionTurn('{"facts":["AI replacement"]}')

    async def aclose(self) -> None:
        return None


class SequenceHistory:
    def __init__(self, *snapshots: list[HistoryEntry]) -> None:
        self.snapshots = list(snapshots)
        self.reads: list[dict[str, Any]] = []

    async def recent(self, *_args, **kwargs) -> list[HistoryEntry]:
        self.reads.append(kwargs)
        if len(self.snapshots) == 1:
            return list(self.snapshots[0])
        return list(self.snapshots.pop(0))


def said(text: str, created_at: datetime | None = None) -> HistoryEntry:
    return HistoryEntry(
        message_id=10,
        sender_id=42,
        role="user",
        text=text,
        created_at=created_at or datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )


async def test_mem_schedule_006_upkeep_runs_once_per_local_day(sessions) -> None:
    """MEM-SCHEDULE-006 — tests/brd/memory.feature"""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.memory_update_time = time(3, 0)
        await session.commit()

    upkeep = StubUpkeep()
    guard = TurnManager()
    now = datetime(2026, 8, 8, 3, 1, tzinfo=ZoneInfo("Europe/Istanbul"))
    first = await run_due_memory_maintenance(
        cast(Any, upkeep),
        sessions,
        42,
        "Europe/Istanbul",
        run_background=guard.run_background,
        now=now,
    )
    second = await run_due_memory_maintenance(
        cast(Any, upkeep),
        sessions,
        42,
        "Europe/Istanbul",
        run_background=guard.run_background,
        now=now,
    )

    assert first is True
    assert second is False
    assert upkeep.calls == [42]
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
        assert state.memory_last_run_at is not None


async def test_mem_schedule_007_upkeep_set_to_off_never_runs(sessions) -> None:
    """MEM-SCHEDULE-007 — tests/brd/memory.feature"""
    upkeep = StubUpkeep()
    ran = await run_due_memory_maintenance(
        cast(Any, upkeep),
        sessions,
        42,
        "Europe/Istanbul",
        run_background=TurnManager().run_background,
        now=datetime(2026, 8, 8, 23, 0, tzinfo=ZoneInfo("Europe/Istanbul")),
    )

    assert ran is False
    assert upkeep.calls == []


async def test_due_memory_maintenance_waits_while_foreground_generation_is_active(
    sessions,
) -> None:
    """AG-TURN-024 — tests/brd/tg_agent_shell/agents.feature"""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.memory_update_time = time(3, 0)
        await session.commit()

    upkeep = StubUpkeep()
    guard = TurnManager()
    guard.begin(101)
    ran = await run_due_memory_maintenance(
        cast(Any, upkeep),
        sessions,
        42,
        "Europe/Istanbul",
        run_background=guard.run_background,
        now=datetime(2026, 8, 8, 3, 1, tzinfo=ZoneInfo("Europe/Istanbul")),
    )

    assert ran is False
    assert upkeep.calls == []
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
        assert state is None or state.memory_last_run_at is None


async def test_mem_sync_005_a_stale_write_keeps_the_file_and_the_cursor(
    sessions, tmp_path: Path
) -> None:
    """MEM-SYNC-005 — tests/brd/memory.feature"""
    memory_path = tmp_path / "memory.md"
    memory_path.write_text("Existing fact", encoding="utf-8")
    upkeep = MemoryUpkeep(
        sessions,
        SequenceHistory([said("A durable new fact")]),  # type: ignore[arg-type]
        FileEditingProvider(memory_path),  # type: ignore[arg-type]
        MemoryFileStore(memory_path, sessions),
    )

    result = await upkeep.maintain_memory(42)

    assert result is MemoryMaintenanceResult.INVALID
    assert memory_path.read_text(encoding="utf-8") == "Owner edit during generation.\n"
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
    assert state is None or state.processed_until is None


async def test_mem_sync_004_upkeep_reads_after_its_own_cursor(sessions, tmp_path: Path) -> None:
    """MEM-SYNC-004 — tests/brd/memory.feature"""
    memory_path = tmp_path / "memory.md"
    memory_path.write_text("Existing fact", encoding="utf-8")
    cursor = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    async with sessions() as session:
        session.add(MemorySyncState(id=1, processed_until=cursor))
        await session.commit()
    entry = said("A durable new fact", cursor + timedelta(hours=1))
    history = SequenceHistory([entry])
    upkeep = MemoryUpkeep(
        sessions,
        cast(Any, history),
        SequenceProvider("Retold fact", REPLACEMENT_FACTS),  # type: ignore[arg-type]
        MemoryFileStore(memory_path, sessions),
    )

    result = await upkeep.maintain_memory(42)

    assert result is MemoryMaintenanceResult.UPDATED
    assert history.reads == [{"token_budget": MEMORY_READ_TOKEN_BUDGET, "since": cursor}]
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
    assert state.processed_until == entry.created_at


async def test_mem_file_008_upkeep_refuses_to_replace_an_unreadable_file(
    sessions, tmp_path: Path
) -> None:
    """MEM-FILE-008 — tests/brd/memory.feature"""
    memory_path = tmp_path / "memory.md"
    memory_path.write_bytes(NOT_TEXT)
    provider = RecordingProvider("never asked for")
    upkeep = MemoryUpkeep(
        sessions,
        SequenceHistory([said("A durable new fact")]),  # type: ignore[arg-type]
        cast(Any, provider),
        MemoryFileStore(memory_path, sessions),
    )

    result = await upkeep.maintain_memory(42)

    assert result is MemoryMaintenanceResult.INVALID
    assert provider.requests == []
    assert memory_path.read_bytes() == NOT_TEXT
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
    assert state.processed_until is None
