from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import delete
from ui_harness import FakeMessage, voice_message_for
from usage_harness import START, UsageClock, total, tracked_services

from safwa.features.profile.telegram import command_profile
from safwa.features.profile.telegram.screens import usage_label
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.telegram.services import OwnerAndWritingMiddleware
from tg_agent_shell.usage.model import IDLE_WINDOW
from tg_agent_shell.usage.recorder import UsageRecorder
from tg_agent_shell.usage.use_cases import record_usage, usage_seconds

WINDOW = int(IDLE_WINDOW.total_seconds())


@pytest.mark.parametrize(
    ("records", "at", "expected"),
    [
        ([], 0, 0),
        ([(0, 0)], 30, 30),
        ([(0, 0)], 300, WINDOW),
        ([(0, 0), (90, 90)], 300, 90 + WINDOW),
        ([(0, 0), (WINDOW, WINDOW)], 300, 2 * WINDOW),
        ([(0, 0), (WINDOW + 1, WINDOW + 1)], 300, 2 * WINDOW),
        ([(0, 0), (60, 60), (0, 500)], 700, 500 + WINDOW),
        ([(-60, 0)], 600, 60 + WINDOW),
        ([(0, 0), (86400, 86400)], 86600, 2 * WINDOW),
        ([(60, 60)], 30, 0),
    ],
)
async def test_each_moment_counts_once_with_a_two_minute_quiet_window(
    sessions, records, at, expected,
):
    """TG-USAGE-024 — tests/brd/tg_agent_shell/telegram_history.feature"""
    async with sessions() as session:
        for since, until in records:
            await record_usage(
                session, 42,
                since=START + timedelta(seconds=since), at=START + timedelta(seconds=until),
            )
            await session.flush()
        assert await usage_seconds(session, 42, now=START + timedelta(seconds=at)) == expected


async def test_usage_belongs_to_its_owner(sessions):
    """TG-EVENT-030 — tests/brd/tg_agent_shell/telegram_history.feature"""
    async with sessions() as session:
        await record_usage(session, 43, since=START - timedelta(seconds=500), at=START)
        await record_usage(session, 42, since=START, at=START)
        await session.commit()
        assert await usage_seconds(session, 42, now=START + timedelta(minutes=5)) == WINDOW


@pytest.mark.parametrize(("forwarded", "uploaded", "expected"), [
    (False, False, 60), (True, False, 0), (False, True, 0),
])
async def test_only_own_recordings_add_their_duration(
    sessions, monkeypatch, forwarded, uploaded, expected,
):
    """TG-VOICE-025 — tests/brd/tg_agent_shell/telegram_history.feature"""
    services, clock = tracked_services(sessions, monkeypatch)
    message = voice_message_for(1, duration=60)
    message.forward_origin = object() if forwarded else None
    if uploaded:
        message.audio, message.voice = message.voice, None

    async def handler(event, data):
        return None

    await OwnerAndWritingMiddleware()(handler, message, {"services": services})
    assert await total(services) == expected
    clock.advance(300)
    assert await total(services) == expected + WINDOW


async def test_an_edit_is_activity_and_does_not_repeat_recording_time(sessions, monkeypatch):
    """TG-EVENT-030 — tests/brd/tg_agent_shell/telegram_history.feature"""
    services, clock = tracked_services(sessions, monkeypatch)
    middleware = OwnerAndWritingMiddleware()
    message = voice_message_for(2, duration=60)

    async def handler(event, data):
        return None

    await middleware(handler, message, {"services": services})
    clock.advance(300)
    message.edit_date = clock.now()
    await middleware(handler, message, {"services": services})
    assert await total(services) == 60 + WINDOW
    clock.advance(300)
    assert await total(services) == 60 + 2 * WINDOW


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

    async def refuse_delete():
        raise TelegramAPIError(method=None, message="cannot delete")

    async def handler(event, data):
        clock.advance(300)

    message.delete = refuse_delete
    await OwnerAndWritingMiddleware()(handler, message, {"services": services})
    clock.advance(500)
    assert await total(services) == 300 + WINDOW


async def test_the_wait_ends_with_the_answer_and_later_work_adds_nothing(sessions):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    clock = UsageClock()
    recorder = UsageRecorder(sessions, 42, clock=clock)
    async with recorder.waiting():
        clock.advance(100)
        await recorder.answered()
        clock.advance(3600)
    async with sessions() as session:
        assert await recorder.seconds(session) == 100 + WINDOW


async def test_cancelled_work_ends_the_wait_and_its_window_follows(sessions):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    clock = UsageClock()
    recorder = UsageRecorder(sessions, 42, clock=clock)
    entered = asyncio.Event()

    async def wait():
        async with recorder.waiting():
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
        assert await recorder.seconds(session) == 300 + WINDOW


async def test_unhandled_request_failure_ends_the_wait(sessions):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    clock = UsageClock()
    recorder = UsageRecorder(sessions, 42, clock=clock)
    with pytest.raises(RuntimeError, match="failed"):
        async with recorder.waiting():
            clock.advance(300)
            raise RuntimeError("failed")
    clock.advance(1000)
    async with sessions() as session:
        assert await recorder.seconds(session) == 300 + WINDOW


async def test_a_restart_adds_neither_the_interrupted_wait_nor_downtime(sessions):
    """TG-RESTART-028 — tests/brd/tg_agent_shell/telegram_history.feature"""
    clock = UsageClock()
    async with sessions() as session:
        await record_usage(session, 42, since=clock.now(), at=clock.now() + timedelta(seconds=90))
        await session.commit()
    clock.advance(1000)
    before = UsageRecorder(sessions, 42, clock=clock)
    await before.acted()
    # The process dies while the owner waits, so the wait is never recorded.
    clock.advance(3600)
    after = UsageRecorder(sessions, 42, clock=clock)
    async with sessions() as session:
        assert await after.seconds(session) == 90 + 2 * WINDOW


async def test_clearing_chat_keeps_the_usage(sessions):
    """TG-KEEP-029 — tests/brd/tg_agent_shell/telegram_history.feature"""
    async with sessions() as session:
        await record_usage(session, 42, since=START, at=START)
        await session.execute(delete(TelegramMessage))
        await session.commit()
        assert await usage_seconds(session, 42, now=START + timedelta(days=1)) == WINDOW


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
    assert "<i>Usage time:</i> ~0m" in message.edits[-1][0]
    await services.usage.acted()
    clock.advance(65)
    await command_profile(message, services)
    assert "<i>Usage time:</i> ~1m" in message.edits[-1][0]
    clock.advance(500)
    await command_profile(message, services)
    assert "<i>Usage time:</i> ~2m" in message.edits[-1][0]
