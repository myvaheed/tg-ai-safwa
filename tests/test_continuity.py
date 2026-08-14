from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from safwa.continuity import (
    MemoryMaintenanceResult,
    PersonaContinuity,
    parse_memory_update_time,
    run_due_memory_maintenance,
)
from safwa.enums import MessageKind
from safwa.history import HistoryEntry
from safwa.memory import MemoryFileStore
from safwa.models import MemorySyncState, UserProfile


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

    async def complete(self, *_args, **_kwargs) -> str:
        return self.responses.pop(0)


class SequenceHistory:
    def __init__(self, *snapshots: list[HistoryEntry]) -> None:
        self.snapshots = list(snapshots)

    async def recent(self, *_args, **_kwargs) -> list[HistoryEntry]:
        if len(self.snapshots) == 1:
            return list(self.snapshots[0])
        return list(self.snapshots.pop(0))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("03:00", time(3, 0)), ("23:59", time(23, 59)), ("off", None), ("OFF", None)],
)
def test_parse_memory_update_time(raw: str, expected: time | None) -> None:
    assert parse_memory_update_time(raw) == expected


@pytest.mark.parametrize("raw", ["", "3:00", "24:00", "tomorrow"])
def test_parse_memory_update_time_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError, match="Memory update time"):
        parse_memory_update_time(raw)


async def test_daily_memory_sync_runs_once_after_configured_time(sessions) -> None:
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.memory_update_time = time(3, 0)
        await session.commit()

    continuity = StubContinuity()
    now = datetime(2026, 8, 8, 3, 1, tzinfo=ZoneInfo("Europe/Istanbul"))
    first = await run_due_memory_maintenance(
        cast(Any, continuity), sessions, 42, lambda: False, "Europe/Istanbul", now=now
    )
    second = await run_due_memory_maintenance(
        cast(Any, continuity), sessions, 42, lambda: False, "Europe/Istanbul", now=now
    )

    assert first is True
    assert second is False
    assert continuity.calls == [42]
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
        assert state.memory_last_run_at is not None


async def test_daily_memory_sync_is_off_until_configured(sessions) -> None:
    continuity = StubContinuity()
    ran = await run_due_memory_maintenance(
        cast(Any, continuity),
        sessions,
        42,
        lambda: False,
        "Europe/Istanbul",
        now=datetime(2026, 8, 8, 23, 0, tzinfo=ZoneInfo("Europe/Istanbul")),
    )

    assert ran is False
    assert continuity.calls == []


async def test_daily_memory_sync_waits_for_foreground_generation(sessions) -> None:
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.memory_update_time = time(3, 0)
        await session.commit()

    continuity = StubContinuity()
    ran = await run_due_memory_maintenance(
        cast(Any, continuity),
        sessions,
        42,
        lambda: True,
        "Europe/Istanbul",
        now=datetime(2026, 8, 8, 3, 1, tzinfo=ZoneInfo("Europe/Istanbul")),
    )

    assert ran is False
    assert continuity.calls == []


async def test_invalid_memory_response_keeps_file_and_cursor_unchanged(
    sessions, tmp_path: Path
) -> None:
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
        SequenceProvider("Retold fact", "not-json"),  # type: ignore[arg-type]
        memory,
    )

    result = await continuity.maintain_memory(42)

    assert result is MemoryMaintenanceResult.INVALID
    assert memory_path.read_text(encoding="utf-8") == "Existing fact"
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
    assert state is None or state.processed_message_id is None


async def test_summary_is_discarded_when_history_changes_during_generation(sessions) -> None:
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
    sent: list[tuple[str, int]] = []

    async def send_summary(text: str, covered_id: int) -> None:
        sent.append((text, covered_id))

    assert await continuity.maybe_summarize(42, send_summary) is False
    assert sent == []
