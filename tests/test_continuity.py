from __future__ import annotations

from datetime import datetime, time
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from safwa.continuity import (
    MemoryMaintenanceResult,
    parse_memory_update_time,
    run_due_memory_maintenance,
)
from safwa.models import MemorySyncState, UserProfile


class StubContinuity:
    def __init__(self, result: MemoryMaintenanceResult = MemoryMaintenanceResult.UPDATED) -> None:
        self.result = result
        self.calls: list[int] = []

    async def maintain_memory(self, chat_id: int) -> MemoryMaintenanceResult:
        self.calls.append(chat_id)
        return self.result


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
