from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time

from safwa.domain import create_card
from safwa.enums import CardKind, CardStage
from safwa.models import UserProfile
from safwa.scheduler import ReminderPolicy, run_scheduler


async def _profile(sessions) -> None:
    """Reminders on, nothing suppressing them, no time-window candidates."""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            profile = UserProfile(id=1)
            session.add(profile)
        profile.reminders_enabled = True
        profile.quiet_start = None
        profile.quiet_end = None
        profile.morning_checkin = None
        profile.evening_checkin = None
        profile.wake_time = None
        profile.bed_time = None
        await session.commit()


async def test_today_candidate_builds_a_string_dedupe_key(sessions) -> None:
    """Card ids are ints; joining them without str() used to kill the scheduler task."""
    await _profile(sessions)
    async with sessions() as session:
        await create_card(
            session,
            kind=CardKind.ACTION.value,
            title="Write the report",
            stage=CardStage.TODAY.value,
            effort_points=3,
        )
        await session.commit()

    policy = ReminderPolicy("Europe/Istanbul", now=lambda: datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
    async with sessions() as session:
        candidates = await policy.candidates(session)

    today = [item for item in candidates if item[0] == "today"]
    assert today, "an Action in Today should produce a today candidate"
    assert isinstance(today[0][1], str)
    assert all(isinstance(key, str) for _, key, _ in candidates)


async def test_today_candidate_ignores_goals_and_ideas(sessions) -> None:
    """Dashboards list Actions only; the reminder text must count the same set."""
    await _profile(sessions)
    async with sessions() as session:
        goal = await create_card(session, kind=CardKind.GOAL.value, title="Ship v2")
        await create_card(
            session,
            kind=CardKind.ACTION.value,
            title="Draft the plan",
            parent_id=goal.id,
            stage=CardStage.TODAY.value,
            effort_points=2,
        )
        await session.commit()

    policy = ReminderPolicy("Europe/Istanbul", now=lambda: datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
    async with sessions() as session:
        candidates = await policy.candidates(session)

    today = next(item for item in candidates if item[0] == "today")
    # The Goal's effective stage aggregates to Today, but only the Action is counted.
    assert "1 Action(s)" in today[2]


async def test_repeat_drift_candidate_builds_a_string_dedupe_key(sessions) -> None:
    await _profile(sessions)
    async with sessions() as session:
        await create_card(
            session,
            kind=CardKind.ACTION.value,
            title="Weekly review",
            stage=CardStage.BACKLOG.value,
            effort_points=2,
            repeatable=True,
        )
        await session.commit()

    policy = ReminderPolicy("Europe/Istanbul", now=lambda: datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
    async with sessions() as session:
        candidates = await policy.candidates(session)

    drift = next(item for item in candidates if item[0] == "repeat_drift")
    assert isinstance(drift[1], str)


async def test_scheduler_survives_a_failing_candidate_computation(sessions) -> None:
    """A broken policy must not end the loop; reminders have to keep running."""
    calls = {"count": 0}

    class ExplodingPolicy:
        async def candidates(self, session):  # type: ignore[no-untyped-def]
            calls["count"] += 1
            raise RuntimeError("boom")

    async def send_reminder(_text: str) -> bool:  # pragma: no cover - never reached
        raise AssertionError("no candidate should be delivered")

    task = asyncio.create_task(
        run_scheduler(sessions, ExplodingPolicy(), send_reminder, poll_seconds=0.01)
    )
    for _ in range(200):
        await asyncio.sleep(0.01)
        if calls["count"] >= 2:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert calls["count"] >= 2, "the loop stopped after the first failure"


async def test_memory_update_time_column_accepts_a_time(sessions) -> None:
    """Guards the /setmemtime round-trip the settings screen renders."""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.memory_update_time = time(3, 0)
        await session.commit()
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).memory_update_time == time(3, 0)
