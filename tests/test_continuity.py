from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from safwa.constants import MEMORY_READ_TOKEN_BUDGET
from safwa.continuity import (
    MemoryMaintenanceResult,
    PersonaContinuity,
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


class RecordingProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.requests: list[list[dict[str, str]]] = []

    async def complete(self, messages, **_kwargs) -> str:
        self.requests.append(messages)
        return self.responses.pop(0)


class SequenceHistory:
    def __init__(self, *snapshots: list[HistoryEntry]) -> None:
        self.snapshots = list(snapshots)
        self.reads: list[dict[str, Any]] = []

    async def recent(self, *_args, **kwargs) -> list[HistoryEntry]:
        self.reads.append(kwargs)
        if len(self.snapshots) == 1:
            return list(self.snapshots[0])
        return list(self.snapshots.pop(0))


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
    assert state is None or state.processed_until is None


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


async def test_a_new_summary_rewrites_the_previous_one(sessions) -> None:
    """The window keeps only the newest Summary, so the older one must be folded in."""
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
    sent: list[tuple[str, int]] = []

    async def send_summary(text: str, covered_id: int) -> None:
        sent.append((text, covered_id))

    assert await continuity.maybe_summarize(42, send_summary) is True
    request = provider.requests[0][-1]["content"]
    assert request.startswith("Previous summary:\nEverything before today.")
    assert "A long enough request" in request
    assert sent == [("📜 Summary\nThe rewritten summary", 10)]


async def test_summarize_below_the_trigger_only_happens_when_forced(sessions) -> None:
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
        summary_trigger_tokens=10_000,
    )
    sent: list[tuple[str, int]] = []

    async def send_summary(text: str, covered_id: int) -> None:
        sent.append((text, covered_id))

    assert await continuity.maybe_summarize(42, send_summary) is False
    assert await continuity.maybe_summarize(42, send_summary, force=True) is True
    assert sent == [("📜 Summary\nForced summary", 10)]


async def test_memory_reads_back_to_its_own_cursor(sessions, tmp_path: Path) -> None:
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
    # SQLite hands the cursor back naive; `recent` is what normalizes it to UTC.
    assert history.reads == [
        {"token_budget": MEMORY_READ_TOKEN_BUDGET, "since": cursor.replace(tzinfo=None)}
    ]
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
    assert state.processed_until.replace(tzinfo=UTC) == entry.created_at
