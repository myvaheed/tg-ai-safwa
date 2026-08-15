from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from safwa.constants import REMINDER_CATCHUP_GRACE_MINUTES, REMINDER_FIRE_BATCH
from safwa.models import Reminder, UserProfile
from safwa.reminders import resolve, schedule_columns
from safwa.scheduler import Firing, prepare, run_scheduler, settle, tick

TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)  # a Thursday


async def make_reminder(sessions, *, instruction="Ping me.", due=NOW, **kwargs) -> int:
    schedule = resolve(now=NOW, tz=TZ, **(kwargs or {"interval_minutes": 120}))
    async with sessions() as session:
        reminder = Reminder(
            instruction=instruction, next_fire_at=due, **schedule_columns(schedule)
        )
        session.add(reminder)
        await session.commit()
        return reminder.id


async def load(sessions, reminder_id: int) -> Reminder | None:
    async with sessions() as session:
        return await session.get(Reminder, reminder_id)


class Recorder:
    """Stands in for the background guard and the advisor turn."""

    def __init__(self, *, open_gate=True, delivered=True):
        self.open_gate = open_gate
        self.delivered = delivered
        self.escalated: list[list[Firing]] = []

    async def gate(self) -> bool:
        return self.open_gate

    async def escalate(self, firings: list[Firing]) -> bool:
        self.escalated.append(firings)
        return self.delivered


# --- the gate -------------------------------------------------------------


async def test_a_closed_gate_advances_nothing(sessions):
    reminder_id = await make_reminder(sessions)
    recorder = Recorder(open_gate=False)

    assert await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder)) is False

    assert not recorder.escalated
    reminder = await load(sessions, reminder_id)
    assert reminder.next_fire_at == NOW  # still due, so the next tick retries it
    assert reminder.fire_count == 0


async def test_a_failed_escalation_advances_nothing(sessions):
    reminder_id = await make_reminder(sessions)
    recorder = Recorder(delivered=False)

    assert await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder)) is False

    assert recorder.escalated  # it was attempted
    reminder = await load(sessions, reminder_id)
    assert reminder.next_fire_at == NOW
    assert reminder.fire_count == 0


async def test_a_system_reminder_fires_like_any_other(sessions):
    """It is hidden from the UI and from the model, never from the poll."""
    reminder_id = await make_reminder(sessions)
    async with sessions() as session:
        (await session.get(Reminder, reminder_id)).system = True
        await session.commit()
    recorder = Recorder()

    assert await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder)) is True
    assert recorder.escalated


# --- batching -------------------------------------------------------------


async def test_one_escalation_carries_at_most_the_batch_size(sessions):
    for offset in range(REMINDER_FIRE_BATCH + 2):
        await make_reminder(sessions, due=NOW - timedelta(minutes=offset))
    recorder = Recorder()

    await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder))

    assert len(recorder.escalated) == 1
    assert len(recorder.escalated[0]) == REMINDER_FIRE_BATCH


async def test_the_oldest_due_reminders_go_first(sessions):
    late = await make_reminder(sessions, due=NOW - timedelta(hours=1))
    early = await make_reminder(sessions, due=NOW - timedelta(minutes=1))
    recorder = Recorder()

    await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder))

    assert [firing.reminder_id for firing in recorder.escalated[0]] == [late, early]


# --- one-shots ------------------------------------------------------------


async def test_a_one_shot_is_deleted_only_after_the_turn_succeeds(sessions):
    reminder_id = await make_reminder(
        sessions, clock="09:00", day="20.08.2026", due=NOW
    )
    failing = Recorder(delivered=False)

    await tick(sessions, tz=TZ, now=NOW, **_hooks(failing))
    assert await load(sessions, reminder_id) is not None

    await tick(sessions, tz=TZ, now=NOW, **_hooks(Recorder()))
    assert await load(sessions, reminder_id) is None


async def test_a_one_shot_fires_however_late(sessions):
    """A one-shot ignores the catch-up grace entirely: it produces exactly one escalation."""
    await make_reminder(
        sessions,
        clock="09:00",
        day="20.08.2026",
        due=NOW - timedelta(days=30),
    )
    recorder = Recorder()

    assert await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder)) is True
    assert len(recorder.escalated[0]) == 1


# --- catch-up -------------------------------------------------------------


async def test_a_repeat_inside_the_grace_window_still_fires(sessions):
    reminder_id = await make_reminder(
        sessions, due=NOW - timedelta(minutes=REMINDER_CATCHUP_GRACE_MINUTES - 1)
    )
    recorder = Recorder()

    assert await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder)) is True
    assert (await load(sessions, reminder_id)).fire_count == 1


async def test_a_repeat_past_the_grace_window_rolls_forward_silently(sessions):
    reminder_id = await make_reminder(sessions, due=NOW - timedelta(days=3))
    recorder = Recorder()

    assert await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder)) is False

    assert not recorder.escalated
    reminder = await load(sessions, reminder_id)
    assert reminder.next_fire_at > NOW
    assert reminder.fire_count == 0  # rolled forward is not fired


async def test_a_repeat_advances_from_its_scheduled_moment_not_the_delivery_moment(sessions):
    """A turn that takes four minutes must not push every later fire four minutes out."""
    reminder_id = await make_reminder(sessions, due=NOW, interval_minutes=120)
    delivered_at = NOW + timedelta(minutes=4)

    await tick(sessions, tz=TZ, now=delivered_at, **_hooks(Recorder()))

    assert (await load(sessions, reminder_id)).next_fire_at == NOW + timedelta(hours=2)


# --- direct escalation ----------------------------------------------------


async def test_a_due_reminder_reaches_the_advisor_without_a_preflight_session(sessions):
    reminder_id = await make_reminder(sessions, instruction="Review Card #88.")
    recorder = Recorder()

    await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder))

    firing = recorder.escalated[0][0]
    assert firing.reminder_id == reminder_id
    assert firing.instruction == "Review Card #88."


# --- prepare / settle in isolation ----------------------------------------


async def test_settle_records_a_successful_delivery(sessions):
    reminder_id = await make_reminder(sessions, instruction="Review Card #88.")
    async with sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        firings = await prepare(session, [reminder], now=NOW, tz=TZ)
        await settle(session, firings, now=NOW, tz=TZ)
        await session.commit()

    reminder = await load(sessions, reminder_id)
    assert reminder.last_fired_at == NOW
    assert reminder.fire_count == 1


# --- the poll loop --------------------------------------------------------


async def test_the_loop_survives_a_failing_tick(sessions):
    """A broken tick must not end the loop; reminders have to keep running."""
    await make_reminder(sessions)
    calls = {"count": 0}

    async def gate() -> bool:
        calls["count"] += 1
        raise RuntimeError("boom")

    async def escalate(firings):  # pragma: no cover - never reached
        raise AssertionError("a failing tick delivers nothing")

    task = asyncio.create_task(
        run_scheduler(
            sessions,
            timezone="Europe/Istanbul",
            gate=gate,
            escalate=escalate,
            poll_seconds=0.01,
        )
    )
    for _ in range(200):
        await asyncio.sleep(0.01)
        if calls["count"] >= 2:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls["count"] >= 2


async def test_memory_update_time_column_accepts_a_time(sessions) -> None:
    """Guards the /setmemtime round-trip the settings screen renders."""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.memory_update_time = time(3, 0)
        await session.commit()
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).memory_update_time == time(3, 0)


def _hooks(recorder: Recorder) -> dict:
    return {
        "gate": recorder.gate,
        "escalate": recorder.escalate,
    }
