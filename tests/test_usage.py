from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import delete, select
from ui_harness import FakeMessage, voice_message_for
from usage_harness import START, UsageClock, total, tracked_services

from safwa.features.profile.telegram import command_profile
from safwa.features.profile.telegram.screens import usage_label
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.recovery import recover_startup
from tg_agent_shell.telegram.services import OwnerAndWritingMiddleware
from tg_agent_shell.usage.model import UsageInterval
from tg_agent_shell.usage.recorder import UsageRecorder
from tg_agent_shell.usage.use_cases import (
    checkpoint_processing,
    covered_seconds,
    record_activity,
    start_processing,
    usage_seconds,
)


@pytest.mark.parametrize(
    ("intervals", "at", "expected"),
    [
        ([], 0, 0),
        ([(0, 120)], 30, 30),
        ([(0, 120)], 120, 120),
        ([(0, 120), (90, 210)], 300, 210),
        ([(0, 120), (120, 240)], 300, 240),
        ([(0, 120), (121, 241)], 300, 240),
        ([(0, 500), (60, 120), (300, 360)], 600, 500),
        ([(0, 120), (0, 240), (240, 360)], 600, 360),
        ([(-60, 120), (0, 240), (240, 360)], 600, 420),
        ([(60, 180)], 30, 0),
        ([(0, 120), (86400, 86520)], 86600, 240),
    ],
)
def test_elapsed_intervals_are_a_union(intervals, at, expected):
    """TG-USAGE-024 — tests/brd/tg_agent_shell/telegram_history.feature"""
    assert covered_seconds(
        [(START + timedelta(seconds=a), START + timedelta(seconds=b)) for a, b in intervals],
        now=START + timedelta(seconds=at),
    ) == expected


async def test_activity_is_idempotent_and_scoped_to_the_owner(sessions):
    """TG-EVENT-030 — tests/brd/tg_agent_shell/telegram_history.feature"""
    async with sessions() as session:
        for _ in range(2):
            await record_activity(session, owner_id=42, event_key="message:1", at=START)
        await record_activity(
            session, owner_id=43, event_key="message:1", at=START, voice_seconds=500,
        )
        await session.commit()
        assert len(list(await session.scalars(select(UsageInterval)))) == 2
        assert await usage_seconds(session, 42, now=START + timedelta(minutes=5)) == 120


@pytest.mark.parametrize(("forwarded", "uploaded", "expected"), [
    (False, False, 60), (True, False, 0), (False, True, 0),
])
async def test_only_own_recordings_add_their_duration(
    sessions, monkeypatch, forwarded, uploaded, expected,
):
    """TG-VOICE-025 — tests/brd/tg_agent_shell/telegram_history.feature"""
    services, clock = tracked_services(sessions, monkeypatch)
    message = voice_message_for(1, duration=60)
    message.date = clock.now()
    message.forward_origin = object() if forwarded else None
    if uploaded:
        message.audio, message.voice = message.voice, None

    async def handler(event, data):
        return None

    await OwnerAndWritingMiddleware()(handler, message, {"services": services})
    assert await total(services) == expected
    clock.advance(300)
    assert await total(services) == expected + 120


async def test_an_edit_is_activity_and_does_not_repeat_recording_time(sessions, monkeypatch):
    """TG-EVENT-030 — tests/brd/tg_agent_shell/telegram_history.feature"""
    services, clock = tracked_services(sessions, monkeypatch)
    middleware = OwnerAndWritingMiddleware()
    message = voice_message_for(2, duration=60)
    message.date = clock.now()

    async def handler(event, data):
        return None

    await middleware(handler, message, {"services": services})
    clock.advance(300)
    message.edit_date = clock.now()
    await middleware(handler, message, {"services": services})
    clock.advance(300)
    await middleware(handler, message, {"services": services})
    assert await total(services) == 300
    async with sessions() as session:
        rows = list(await session.scalars(select(UsageInterval)))
        assert len(rows) == 3


async def test_other_users_and_groups_never_start_tracking(sessions, monkeypatch):
    """TG-EVENT-030 — tests/brd/tg_agent_shell/telegram_history.feature"""
    services, clock = tracked_services(sessions, monkeypatch)

    async def handler(event, data):
        pytest.fail("unauthorized input reached the handler")

    message = FakeMessage(3, text="Hello", bot_message=False)
    message.from_user.id = 43
    await OwnerAndWritingMiddleware()(handler, message, {"services": services})
    message.from_user.id = 42
    message.chat.type = "group"
    await OwnerAndWritingMiddleware()(handler, message, {"services": services})
    clock.advance(500)
    assert await total(services) == 0


async def test_a_replacement_message_that_cannot_be_deleted_counts_its_wait(sessions, monkeypatch):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    services, clock = tracked_services(sessions, monkeypatch)
    services.turn.begin(10)
    message = FakeMessage(11, text="Actually, this instead", bot_message=False)
    message.date = clock.now()

    async def refuse_delete():
        raise TelegramAPIError(method=None, message="cannot delete")

    async def handler(event, data):
        clock.advance(300)

    message.delete = refuse_delete
    await OwnerAndWritingMiddleware()(handler, message, {"services": services})
    clock.advance(500)
    assert await total(services) == 420


async def test_cancelled_work_closes_without_a_reading_tail(sessions):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    clock = UsageClock()
    recorder = UsageRecorder(sessions, 42, clock=clock, ticks=clock.ticks)
    entered = asyncio.Event()

    async def wait():
        async with recorder.processing("cancelled"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(wait())
    await entered.wait()
    clock.advance(300)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    clock.advance(500)
    async with sessions() as session:
        assert await recorder.seconds(session) == 300
        row = await session.scalar(select(UsageInterval))
        assert not row.active


@pytest.mark.parametrize("cancel_at", ["commit", "session_exit"])
async def test_cancellation_while_opening_closes_the_committed_wait(sessions, cancel_at):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    clock = UsageClock()
    committed = asyncio.Event()
    worker = None
    opened = False

    @asynccontextmanager
    async def opening_sessions():
        nonlocal opened
        opening = asyncio.current_task() is worker and not opened
        opened |= opening
        async with sessions() as session:
            if opening and cancel_at == "commit":
                original = session.commit

                async def paused_commit():
                    await original()
                    committed.set()
                    await asyncio.Event().wait()

                session.commit = paused_commit
            yield session
            if opening and cancel_at == "session_exit":
                committed.set()
                await asyncio.Event().wait()

    recorder = UsageRecorder(opening_sessions, 42, clock=clock, ticks=clock.ticks)

    async def work():
        async with recorder.processing("cancel-at-start"):
            pytest.fail("cancelled startup reached the handler")

    worker = asyncio.create_task(work())
    try:
        await asyncio.wait_for(committed.wait(), timeout=5)
        clock.advance(5)
    finally:
        worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    clock.advance(3600)
    async with sessions() as session:
        row = await session.scalar(select(UsageInterval))
        assert not row.active
        assert await recorder.seconds(session) == 5


@pytest.mark.parametrize(("next_owner", "next_key"), [(42, "next"), (43, "cancelled")])
async def test_cancelled_insert_cannot_close_an_interval_that_reuses_its_id(
    sessions, next_owner, next_key,
):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    clock = UsageClock()
    opening = True

    @asynccontextmanager
    async def cancelling_sessions():
        nonlocal opening
        async with sessions() as session:
            if opening:
                opening = False

                async def cancelled_commit():
                    await session.rollback()
                    async with sessions() as replacement:
                        await start_processing(
                            replacement, owner_id=next_owner, event_key=next_key, at=clock.now(),
                        )
                        await replacement.commit()
                    raise asyncio.CancelledError

                session.commit = cancelled_commit
            yield session

    recorder = UsageRecorder(cancelling_sessions, 42, clock=clock, ticks=clock.ticks)
    with pytest.raises(asyncio.CancelledError):
        async with recorder.processing("cancelled"):
            pytest.fail("cancelled insert reached the handler")
    async with sessions() as session:
        row = await session.scalar(select(UsageInterval))
        assert row.id == 1
        assert row.active
        assert row.owner_id == next_owner
        assert row.event_key == f"processing:{next_key}"


async def test_temporary_checkpoint_failure_does_not_stop_the_next_checkpoint(
    sessions, monkeypatch,
):
    """TG-RESTART-028 — tests/brd/tg_agent_shell/telegram_history.feature"""
    monkeypatch.setattr("tg_agent_shell.usage.recorder.CHECKPOINT_SECONDS", 0.01)
    clock = UsageClock()
    recorder = UsageRecorder(sessions, 42, clock=clock, ticks=clock.ticks)
    original = recorder._checkpoint
    retried = asyncio.Event()
    calls = 0

    async def checkpoint(work):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary database failure")
        active = await original(work)
        retried.set()
        return active

    monkeypatch.setattr(recorder, "_checkpoint", checkpoint)
    async with recorder.processing("retry-checkpoint"):
        clock.advance(90)
        await asyncio.wait_for(retried.wait(), timeout=1)
        async with sessions() as session:
            row = await session.scalar(select(UsageInterval))
            assert row.active
            assert row.ended_at == clock.now()


async def test_a_restart_keeps_only_the_last_confirmed_checkpoint(sessions):
    """TG-RESTART-028 — tests/brd/tg_agent_shell/telegram_history.feature"""
    async with sessions() as session:
        interval_id = await start_processing(session, owner_id=42, event_key="crash", at=START)
        await checkpoint_processing(session, interval_id, at=START + timedelta(seconds=90))
        await session.commit()
        await recover_startup(session)
        await session.commit()
        assert await usage_seconds(session, 42, now=START + timedelta(days=2)) == 90
        assert not (await session.get(UsageInterval, interval_id)).active


async def test_unhandled_request_failure_ends_the_wait(sessions):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    clock = UsageClock()
    recorder = UsageRecorder(sessions, 42, clock=clock, ticks=clock.ticks)
    with pytest.raises(RuntimeError, match="failed"):
        async with recorder.processing("failed"):
            clock.advance(300)
            raise RuntimeError("failed")
    clock.advance(1000)
    async with sessions() as session:
        assert await recorder.seconds(session) == 300
        assert not (await session.scalar(select(UsageInterval))).active


async def test_long_work_checkpoints_and_uses_monotonic_elapsed_time(sessions, monkeypatch):
    """TG-RESTART-028 — tests/brd/tg_agent_shell/telegram_history.feature"""
    monkeypatch.setattr("tg_agent_shell.usage.recorder.CHECKPOINT_SECONDS", 0.01)
    clock = UsageClock()
    recorder = UsageRecorder(sessions, 42, clock=clock, ticks=clock.ticks)
    async with recorder.processing("long"):
        clock.advance(60)
        for _ in range(100):
            async with sessions() as session:
                row = await session.scalar(select(UsageInterval))
                if row.ended_at == clock.now():
                    break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("work was not checkpointed")
        clock.at -= timedelta(hours=1)
        clock.elapsed += 60
    async with sessions() as session:
        row = await session.scalar(select(UsageInterval))
        assert row.ended_at == START + timedelta(seconds=240)
        assert not row.active


async def test_clearing_chat_keeps_the_usage_intervals(sessions):
    """TG-KEEP-029 — tests/brd/tg_agent_shell/telegram_history.feature"""
    async with sessions() as session:
        await record_activity(session, owner_id=42, event_key="message:1", at=START)
        await session.execute(delete(TelegramMessage))
        await session.commit()
        assert await usage_seconds(session, 42, now=START + timedelta(days=1)) == 120


@pytest.mark.parametrize(("seconds", "expected"), [
    (0, "~0m"), (59, "~0m"), (60, "~1m"), (1500, "~25m"), (3600, "~1h"),
    (19500, "~5h 25m"), (86400, "~1d"), (174300, "~2d 25m"),
    (192300, "~2d 5h 25m"), (192359, "~2d 5h 25m"),
])
def test_profile_usage_format(seconds, expected):
    """PS-USAGE-022 — tests/brd/profile.feature"""
    assert usage_label(seconds) == expected


async def test_profile_recalculates_usage_when_it_opens(sessions, monkeypatch):
    """PS-USAGE-022 — tests/brd/profile.feature"""
    services, clock = tracked_services(sessions, monkeypatch)
    message = FakeMessage(4, bot_message=True, answer_as_new=True)
    await command_profile(message, services)
    assert "Usage time: ~0m" in message.edits[-1][0]
    await services.usage.activity("message:1", at=clock.now())
    clock.advance(65)
    await command_profile(message, services)
    assert "Usage time: ~1m" in message.edits[-1][0]
    clock.advance(500)
    await command_profile(message, services)
    assert "Usage time: ~2m" in message.edits[-1][0]
