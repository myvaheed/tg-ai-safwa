from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from llm_gateway import CompletionRequest, CompletionTurn
from safwa.constants import MEMORY_READ_TOKEN_BUDGET, SUMMARY_TRIGGER_TOKENS
from safwa.features.continuity.memory import MemoryFileStore
from safwa.features.continuity.model import SUMMARY_HEADER, MemorySyncState
from safwa.features.continuity.persona import MemoryMaintenanceResult, PersonaContinuity
from safwa.features.continuity.use_cases import run_due_memory_maintenance
from safwa.features.profile.model import UserProfile
from telegram_llm import HistoryEntry
from tg_agent_shell.adapters.kinds import MessageKind
from tg_agent_shell.turn import TurnManager

NOT_TEXT = b"\xff\xfe not text at all"


class StubContinuity:
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


async def test_due_memory_maintenance_runs_once_per_local_day(sessions) -> None:
    """CO-SCHEDULE-009 — tests/brd/continuity.feature"""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.memory_update_time = time(3, 0)
        await session.commit()

    continuity = StubContinuity()
    guard = TurnManager()
    now = datetime(2026, 8, 8, 3, 1, tzinfo=ZoneInfo("Europe/Istanbul"))
    first = await run_due_memory_maintenance(
        cast(Any, continuity),
        sessions,
        42,
        "Europe/Istanbul",
        run_background=guard.run_background,
        now=now,
    )
    second = await run_due_memory_maintenance(
        cast(Any, continuity),
        sessions,
        42,
        "Europe/Istanbul",
        run_background=guard.run_background,
        now=now,
    )

    assert first is True
    assert second is False
    assert continuity.calls == [42]
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
        assert state.memory_last_run_at is not None


async def test_memory_maintenance_off_never_runs(sessions) -> None:
    """CO-SCHEDULE-010 — tests/brd/continuity.feature"""
    continuity = StubContinuity()
    ran = await run_due_memory_maintenance(
        cast(Any, continuity),
        sessions,
        42,
        "Europe/Istanbul",
        run_background=TurnManager().run_background,
        now=datetime(2026, 8, 8, 23, 0, tzinfo=ZoneInfo("Europe/Istanbul")),
    )

    assert ran is False
    assert continuity.calls == []


async def test_background_gate_does_not_start_work_while_foreground_is_active() -> None:
    """CO-GENERATION-011 — tests/brd/continuity.feature"""
    guard = TurnManager()
    guard.begin(101)
    called = False

    async def background(_still_current) -> bool:
        nonlocal called
        called = True
        return True

    assert await guard.run_background(background) is None
    assert called is False


async def test_background_gate_invalidates_currentness_after_dialogue_revision_changes() -> None:
    """CO-GENERATION-011 — tests/brd/continuity.feature"""
    guard = TurnManager()

    async def background(still_current) -> bool:
        assert still_current() is True
        guard.cancel()
        return still_current()

    assert await guard.run_background(background) is False


async def test_due_memory_maintenance_waits_while_foreground_generation_is_active(
    sessions,
) -> None:
    """CO-GENERATION-011 — tests/brd/continuity.feature"""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.memory_update_time = time(3, 0)
        await session.commit()

    continuity = StubContinuity()
    guard = TurnManager()
    guard.begin(101)
    ran = await run_due_memory_maintenance(
        cast(Any, continuity),
        sessions,
        42,
        "Europe/Istanbul",
        run_background=guard.run_background,
        now=datetime(2026, 8, 8, 3, 1, tzinfo=ZoneInfo("Europe/Istanbul")),
    )

    assert ran is False
    assert continuity.calls == []
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
        assert state is None or state.memory_last_run_at is None


async def test_stale_memory_write_keeps_local_file_and_cursor_unchanged(
    sessions, tmp_path: Path
) -> None:
    """CO-SYNC-008 — tests/brd/continuity.feature"""
    memory_path = tmp_path / "memory.md"
    memory_path.write_text("Existing fact", encoding="utf-8")
    entry = HistoryEntry(
        message_id=10,
        sender_id=42,
        role="user",
        text="A durable new fact",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    memory = MemoryFileStore(memory_path, sessions)
    continuity = PersonaContinuity(
        sessions,
        SequenceHistory([entry]),  # type: ignore[arg-type]
        FileEditingProvider(memory_path),  # type: ignore[arg-type]
        memory,
    )

    result = await continuity.maintain_memory(42)

    assert result is MemoryMaintenanceResult.INVALID
    assert memory_path.read_text(encoding="utf-8") == "Owner edit during generation.\n"
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
    assert state is None or state.processed_until is None


async def test_owner_message_wins_race_with_in_flight_summary(sessions) -> None:
    """CO-SUMMARY-003 — tests/brd/continuity.feature"""
    first = HistoryEntry(
        message_id=10,
        sender_id=42,
        role="user",
        text="A long enough request",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    changed = HistoryEntry(
        message_id=11,
        sender_id=42,
        role="user",
        text="A newer request",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    continuity = PersonaContinuity(
        sessions,
        SequenceHistory([first], [first, changed]),  # type: ignore[arg-type]
        SequenceProvider("stale summary"),  # type: ignore[arg-type]
        cast(Any, None),
        summary_trigger_tokens=1,
        chars_per_token=1,
    )
    sent: list[str] = []

    async def write(text: str) -> None:
        sent.append(text)

    assert await continuity.close_window(42, write) is False
    assert sent == []


async def test_new_summary_request_includes_previous_summary(sessions) -> None:
    """CO-SUMMARY-002 — tests/brd/continuity.feature"""
    previous = HistoryEntry(
        message_id=9,
        sender_id=99,
        role="user",
        text="Everything before today.",
        created_at=datetime.now(UTC),
        kind=MessageKind.SUMMARY.value,
    )
    entry = HistoryEntry(
        message_id=10,
        sender_id=42,
        role="user",
        text="A long enough request",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    provider = RecordingProvider("The rewritten summary")
    continuity = PersonaContinuity(
        sessions,
        SequenceHistory([previous, entry]),  # type: ignore[arg-type]
        cast(Any, provider),
        cast(Any, None),
        summary_trigger_tokens=1,
        chars_per_token=1,
    )
    sent: list[str] = []

    async def write(text: str) -> None:
        sent.append(text)

    assert await continuity.close_window(42, write) is True
    request = provider.requests[0].messages[-1]["content"]
    assert request.startswith("Everything before today.")
    assert "A long enough request" in request
    assert sent == [f"{SUMMARY_HEADER}\nThe rewritten summary"]


async def test_summary_below_configured_trigger_requires_force(sessions) -> None:
    """CO-SUMMARY-001 — tests/brd/continuity.feature"""
    entry = HistoryEntry(
        message_id=10,
        sender_id=42,
        role="user",
        text="Short",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    provider = RecordingProvider("Forced summary")
    continuity = PersonaContinuity(
        sessions,
        SequenceHistory([entry]),  # type: ignore[arg-type]
        cast(Any, provider),
        cast(Any, None),
        summary_trigger_tokens=SUMMARY_TRIGGER_TOKENS,
    )
    sent: list[str] = []

    async def write(text: str) -> None:
        sent.append(text)

    assert await continuity.close_window(42, write) is False
    assert await continuity.close_window(42, write, force=True) is True
    assert sent == [f"{SUMMARY_HEADER}\nForced summary"]


async def test_memory_maintenance_reads_after_its_own_cursor(sessions, tmp_path: Path) -> None:
    """CO-SYNC-007 — tests/brd/continuity.feature"""
    memory_path = tmp_path / "memory.md"
    memory_path.write_text("Existing fact", encoding="utf-8")
    cursor = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    async with sessions() as session:
        session.add(MemorySyncState(id=1, processed_until=cursor))
        await session.commit()
    entry = HistoryEntry(
        message_id=10,
        sender_id=42,
        role="user",
        text="A durable new fact",
        created_at=cursor + timedelta(hours=1),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    history = SequenceHistory([entry])
    continuity = PersonaContinuity(
        sessions,
        cast(Any, history),
        SequenceProvider("Retold fact", '{"facts":["A durable new fact"]}'),  # type: ignore[arg-type]
        MemoryFileStore(memory_path, sessions),
    )

    result = await continuity.maintain_memory(42)

    assert result is MemoryMaintenanceResult.UPDATED
    assert history.reads == [{"token_budget": MEMORY_READ_TOKEN_BUDGET, "since": cursor}]
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
    assert state.processed_until == entry.created_at


async def test_memory_maintenance_refuses_to_replace_an_unreadable_file(
    sessions, tmp_path: Path
) -> None:
    """CO-MEMORY-012 — tests/brd/continuity.feature"""
    memory_path = tmp_path / "memory.md"
    memory_path.write_bytes(NOT_TEXT)
    entry = HistoryEntry(
        message_id=10,
        sender_id=42,
        role="user",
        text="A durable new fact",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    provider = RecordingProvider("never asked for")
    continuity = PersonaContinuity(
        sessions,
        SequenceHistory([entry]),  # type: ignore[arg-type]
        cast(Any, provider),
        MemoryFileStore(memory_path, sessions),
    )

    result = await continuity.maintain_memory(42)

    assert result is MemoryMaintenanceResult.INVALID
    assert provider.requests == []
    assert memory_path.read_bytes() == NOT_TEXT
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
    assert state.processed_until is None
