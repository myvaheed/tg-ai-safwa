from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.constants import (
    REMINDER_CATCHUP_GRACE_MINUTES,
    REMINDER_FIRE_BATCH,
    REMINDER_MIN_INTERVAL_MINUTES,
)
from safwa.cues.queue import cue_advisor
from safwa.features.reminders import background
from safwa.features.reminders.background import (
    prepare,
    run_scheduler,
    settle,
    tick,
)
from safwa.features.reminders.schedule import resolve, schedule_columns
from safwa.models import Cue, Reminder, UserProfile, Workspace

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


async def said(sessions) -> list[str]:
    """Everything waiting to be said, oldest first."""
    async with sessions() as session:
        return [cue.text for cue in await session.scalars(select(Cue).order_by(Cue.id))]


# --- writing the words down -----------------------------------------------


async def test_the_words_are_written_down_before_the_reminder_moves_on(sessions):
    """RM-FIRE-013 — tests/brd/reminders.feature"""
    reminder_id = await make_reminder(sessions)

    assert await tick(sessions, tz=TZ, now=NOW) is True

    assert len(await said(sessions)) == 1
    reminder = await load(sessions, reminder_id)
    assert reminder.next_fire_at > NOW


async def test_nothing_is_written_while_something_is_still_waiting(sessions):
    """RM-GATE-017 — tests/brd/reminders.feature"""
    reminder_id = await make_reminder(sessions)
    async with sessions() as session:
        await cue_advisor(session, text="Sprint 1 is over.")
        await session.commit()

    assert await tick(sessions, tz=TZ, now=NOW) is False

    assert await said(sessions) == ["Sprint 1 is over."]
    reminder = await load(sessions, reminder_id)
    assert reminder.next_fire_at == NOW  # still due, so a later tick takes it


async def test_a_system_reminder_fires_like_any_other(sessions):
    """RM-SYSTEM-022 — tests/brd/reminders.feature"""
    # It is hidden from the UI and from the model, never from the poll.
    reminder_id = await make_reminder(sessions)
    async with sessions() as session:
        (await session.get(Reminder, reminder_id)).system = True
        await session.commit()

    assert await tick(sessions, tz=TZ, now=NOW) is True
    assert await said(sessions)


# --- batching -------------------------------------------------------------


async def test_one_cue_carries_at_most_the_batch_size(sessions):
    """RM-FIRE-012 — tests/brd/reminders.feature"""
    for offset in range(REMINDER_FIRE_BATCH + 2):
        await make_reminder(sessions, due=NOW - timedelta(minutes=offset))

    await tick(sessions, tz=TZ, now=NOW)

    waiting = await said(sessions)
    assert len(waiting) == 1
    assert waiting[0].startswith(f"{REMINDER_FIRE_BATCH} Reminders triggered.")


async def test_the_oldest_due_reminders_go_first(sessions):
    """RM-FIRE-012 — tests/brd/reminders.feature"""
    late = await make_reminder(sessions, due=NOW - timedelta(hours=1))
    early = await make_reminder(sessions, due=NOW - timedelta(minutes=1))

    await tick(sessions, tz=TZ, now=NOW)

    words = (await said(sessions))[0]
    assert words.index(f"1. Reminder #{late}") < words.index(f"2. Reminder #{early}")


# --- one-shots ------------------------------------------------------------


async def test_a_one_shot_is_deleted_the_moment_its_words_are_written_down(sessions):
    """RM-FIRE-016 — tests/brd/reminders.feature"""
    reminder_id = await make_reminder(
        sessions, clock="09:00", day="20.08.2026", due=NOW
    )

    assert await tick(sessions, tz=TZ, now=NOW) is True

    # What must not be lost now is the Cue, and keeping that is the delivery poll's job.
    assert await load(sessions, reminder_id) is None
    assert len(await said(sessions)) == 1


async def test_a_one_shot_fires_however_late(sessions):
    """RM-FIRE-016 — tests/brd/reminders.feature"""
    # A one-shot ignores the catch-up grace entirely: it produces exactly one firing.
    await make_reminder(
        sessions,
        clock="09:00",
        day="20.08.2026",
        due=NOW - timedelta(days=30),
    )

    assert await tick(sessions, tz=TZ, now=NOW) is True
    assert (await said(sessions))[0].startswith("1 Reminder triggered.")


# --- catch-up -------------------------------------------------------------


async def test_a_repeat_inside_the_grace_window_still_fires(sessions):
    """RM-CATCHUP-019 — tests/brd/reminders.feature"""
    reminder_id = await make_reminder(
        sessions, due=NOW - timedelta(minutes=REMINDER_CATCHUP_GRACE_MINUTES - 1)
    )

    assert await tick(sessions, tz=TZ, now=NOW) is True
    assert (await load(sessions, reminder_id)).next_fire_at > NOW


async def test_a_repeat_past_the_grace_window_rolls_forward_silently(sessions):
    """RM-CATCHUP-019 — tests/brd/reminders.feature"""
    reminder_id = await make_reminder(sessions, due=NOW - timedelta(days=3))

    assert await tick(sessions, tz=TZ, now=NOW) is False

    assert await said(sessions) == []
    reminder = await load(sessions, reminder_id)
    assert reminder.next_fire_at > NOW


async def test_a_frequent_repeat_is_judged_by_its_stored_fire_not_its_rhythm(sessions):
    """RM-CATCHUP-019 — tests/brd/reminders.feature"""
    # The grace measures how long this Reminder went unanswered, not how recently the
    # schedule would have produced an occurrence: three hours of silence is three hours
    # whether it repeats every five minutes or once a day.
    reminder_id = await make_reminder(
        sessions, due=NOW - timedelta(hours=3), interval_minutes=REMINDER_MIN_INTERVAL_MINUTES
    )

    assert await tick(sessions, tz=TZ, now=NOW) is False

    assert await said(sessions) == []  # not 36 messages, and not one either
    assert (await load(sessions, reminder_id)).next_fire_at > NOW


async def test_a_firing_leaves_the_workspace_revision_alone(sessions):
    """RM-FIRE-014 — tests/brd/reminders.feature"""
    await make_reminder(sessions)
    async with sessions() as session:
        before = (await session.get(Workspace, 1)).revision

    assert await tick(sessions, tz=TZ, now=NOW) is True

    async with sessions() as session:
        assert (await session.get(Workspace, 1)).revision == before


async def test_a_repeat_advances_from_its_scheduled_moment_not_the_tick_that_took_it(sessions):
    """RM-FIRE-015 — tests/brd/reminders.feature"""
    # A tick four minutes late must not push every later fire four minutes out.
    reminder_id = await make_reminder(sessions, due=NOW, interval_minutes=120)
    picked_up_at = NOW + timedelta(minutes=4)

    await tick(sessions, tz=TZ, now=picked_up_at)

    assert (await load(sessions, reminder_id)).next_fire_at == NOW + timedelta(hours=2)


# --- the cue itself ----------------------------------------------------


async def test_a_due_reminder_reaches_the_advisor_without_a_preflight_session(sessions):
    """RM-FIRE-011 — tests/brd/reminders.feature"""
    reminder_id = await make_reminder(sessions, instruction="Review Card #88.")

    await tick(sessions, tz=TZ, now=NOW)

    words = (await said(sessions))[0]
    assert f"Reminder #{reminder_id}" in words
    assert "Review Card #88." in words


# --- prepare / settle in isolation ----------------------------------------


async def test_settle_advances_a_repeat(sessions):
    """RM-FIRE-013 — tests/brd/reminders.feature"""
    reminder_id = await make_reminder(sessions, instruction="Review Card #88.")
    async with sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        firings = await prepare(session, [reminder], now=NOW, tz=TZ)
        await settle(session, firings, now=NOW, tz=TZ)
        await session.commit()

    reminder = await load(sessions, reminder_id)
    assert reminder.next_fire_at > NOW


# --- the poll loop --------------------------------------------------------


async def test_the_loop_survives_a_failing_tick(sessions, monkeypatch):
    """RM-POLL-021 — tests/brd/reminders.feature"""
    # A broken tick must not end the loop; reminders have to keep running.
    await make_reminder(sessions)
    calls = {"count": 0}

    async def exploding_next_cue(session):
        calls["count"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(background, "next_cue", exploding_next_cue)
    task = asyncio.create_task(
        run_scheduler(sessions, timezone="Europe/Istanbul", poll_seconds=0.01)
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
