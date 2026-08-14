from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.constants import REMINDER_CATCHUP_GRACE_MINUTES, REMINDER_FIRE_BATCH
from safwa.domain import snooze_reminders
from safwa.enums import RelevanceVerdict
from safwa.models import Reminder, UserProfile, Workspace
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
    """Stands in for the relevance session and the advisor turn."""

    def __init__(self, *, open_gate=True, delivered=True, verdict=RelevanceVerdict.TRIGGER):
        self.open_gate = open_gate
        self.delivered = delivered
        self.verdict = verdict
        self.evaluated: list[int] = []
        self.escalated: list[list[Firing]] = []

    async def gate(self) -> bool:
        return self.open_gate

    async def evaluate(self, reminder: Reminder):
        self.evaluated.append(reminder.id)
        return self.verdict, f"state of {reminder.id}"

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


async def test_reminders_disabled_stops_the_tick(sessions):
    await make_reminder(sessions)
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.reminders_enabled = False
        await session.commit()
    recorder = Recorder()

    assert await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder)) is False
    assert not recorder.escalated


async def test_a_snooze_stops_the_tick_until_it_expires(sessions):
    await make_reminder(sessions)
    async with sessions() as session:
        await snooze_reminders(session, NOW + timedelta(hours=1))
        await session.commit()
    recorder = Recorder()

    assert await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder)) is False
    assert await tick(sessions, tz=TZ, now=NOW + timedelta(hours=2), **_hooks(recorder)) is True


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


# --- the two relevance skips ----------------------------------------------


async def test_an_instruction_without_an_id_never_runs_the_session(sessions):
    await make_reminder(sessions, instruction="Ask me what to start with today.")
    recorder = Recorder()

    await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder))

    assert recorder.evaluated == []
    firing = recorder.escalated[0][0]
    assert firing.verdict is RelevanceVerdict.TRIGGER
    assert firing.state is None


async def test_an_instruction_with_an_id_runs_the_session(sessions):
    reminder_id = await make_reminder(sessions, instruction="Review Card #88.")
    recorder = Recorder()

    await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder))

    assert recorder.evaluated == [reminder_id]
    assert recorder.escalated[0][0].state == f"state of {reminder_id}"


async def test_an_unchanged_revision_reuses_the_cached_verdict(sessions):
    reminder_id = await make_reminder(sessions, instruction="Review Card #88.")
    recorder = Recorder()

    await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder))
    await tick(sessions, tz=TZ, now=NOW + timedelta(hours=2), **_hooks(recorder))

    assert recorder.evaluated == [reminder_id]  # evaluated once, not twice
    assert recorder.escalated[1][0].state == f"state of {reminder_id}"


async def test_a_changed_revision_re_runs_the_session(sessions):
    reminder_id = await make_reminder(sessions, instruction="Review Card #88.")
    recorder = Recorder()

    await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder))
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        workspace.revision += 1
        await session.commit()
    await tick(sessions, tz=TZ, now=NOW + timedelta(hours=2), **_hooks(recorder))

    assert recorder.evaluated == [reminder_id, reminder_id]


async def test_advancing_next_fire_at_does_not_bump_the_workspace_revision(sessions):
    """The revision skip only works because the scheduler's own writes are invisible to it."""
    await make_reminder(sessions, instruction="Review Card #88.")
    async with sessions() as session:
        before = (await session.get(Workspace, 1)).revision

    await tick(sessions, tz=TZ, now=NOW, **_hooks(Recorder()))

    async with sessions() as session:
        assert (await session.get(Workspace, 1)).revision == before


# --- both verdicts escalate -----------------------------------------------


@pytest.mark.parametrize(
    "verdict", [RelevanceVerdict.TRIGGER, RelevanceVerdict.IRRELEVANT]
)
async def test_both_verdicts_escalate(sessions, verdict):
    await make_reminder(sessions, instruction="Review Card #88.")
    recorder = Recorder(verdict=verdict)

    assert await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder)) is True
    assert recorder.escalated[0][0].verdict is verdict


async def test_irrelevant_does_not_delete_the_row_by_itself(sessions):
    """Deletion is the advisor's proposal and the owner's Save, never a silent side effect."""
    reminder_id = await make_reminder(sessions, instruction="Review Card #88.")
    recorder = Recorder(verdict=RelevanceVerdict.IRRELEVANT)

    await tick(sessions, tz=TZ, now=NOW, **_hooks(recorder))

    assert await load(sessions, reminder_id) is not None


# --- prepare / settle in isolation ----------------------------------------


async def test_settle_records_the_verdict_cache(sessions):
    reminder_id = await make_reminder(sessions, instruction="Review Card #88.")
    async with sessions() as session:
        revision = (await session.get(Workspace, 1)).revision
        reminders = list(await session.scalars(select(Reminder)))
        firings = await prepare(
            session, reminders, now=NOW, tz=TZ, evaluate=Recorder().evaluate
        )
        await settle(session, firings, now=NOW, tz=TZ)
        await session.commit()

    reminder = await load(sessions, reminder_id)
    assert reminder.evaluated_revision == revision
    assert reminder.last_verdict == RelevanceVerdict.TRIGGER.value
    assert reminder.last_state == f"state of {reminder_id}"
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

    async def evaluate(reminder):  # pragma: no cover - never reached
        raise AssertionError("a failing tick reaches no reminder")

    async def escalate(firings):  # pragma: no cover - never reached
        raise AssertionError("a failing tick delivers nothing")

    task = asyncio.create_task(
        run_scheduler(
            sessions,
            timezone="Europe/Istanbul",
            gate=gate,
            evaluate=evaluate,
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
        "evaluate": recorder.evaluate,
        "escalate": recorder.escalate,
    }
