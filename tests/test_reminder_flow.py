from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.ai.context import DialogueMessage, board_context
from safwa.ai.service import AIOutcome
from safwa.constants import REMINDER_CATCHUP_GRACE_MINUTES
from safwa.cues.runtime import CueRuntime
from safwa.domain import (
    DomainError,
)
from safwa.enums import MessageKind, ProposalStatus, ScheduleKind
from safwa.features.reminders.background import Firing, format_cue
from safwa.features.reminders.schedule import (
    resolve,
    schedule_columns,
    schedule_from_payload,
    schedule_of,
    schedule_payload,
)
from safwa.features.reminders.use_cases import (
    create_reminder,
    delete_reminder,
    reconcile_reminders,
    reschedule_reminder,
    update_reminder_text,
)
from safwa.models import (
    AgentRun,
    AgentStep,
    ChangeProposal,
    Reminder,
    TelegramMessage,
    Workspace,
)
from safwa.telegram._core import BACKGROUND_SOURCE_ID, GenerationGuard

TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)  # a Thursday


# --- domain ---------------------------------------------------------------


async def test_create_reminder_computes_its_first_fire(sessions):
    """RM-WRITE-008 — tests/brd/reminders.feature"""
    schedule = resolve(days=["Mon"], clock="08:30", now=NOW, tz=TZ)
    async with sessions() as session:
        reminder = await create_reminder(
            session, instruction="Review Card #88.", schedule=schedule, tz=TZ
        )
        await session.commit()
        assert reminder.next_fire_at > datetime.now(UTC)
        assert schedule_of(reminder) == schedule


async def test_create_reminder_rejects_empty_text(sessions):
    """RM-WRITE-008 — tests/brd/reminders.feature"""
    schedule = resolve(interval_minutes=120, now=NOW, tz=TZ)
    async with sessions() as session:
        with pytest.raises(DomainError, match="cannot be empty"):
            await create_reminder(session, instruction="   ", schedule=schedule, tz=TZ)


async def test_editing_text_leaves_the_schedule_alone(sessions):
    """RM-WRITE-009 — tests/brd/reminders.feature"""
    schedule = resolve(interval_minutes=120, now=NOW, tz=TZ)
    async with sessions() as session:
        reminder = await create_reminder(
            session, instruction="Review Card #88.", schedule=schedule, tz=TZ
        )
        await session.commit()
        before = reminder.next_fire_at

        await update_reminder_text(session, reminder.id, "Review Card #99 instead.")
        await session.commit()

    async with sessions() as session:
        reminder = await session.get(Reminder, reminder.id)
        assert reminder.next_fire_at == before
        assert schedule_of(reminder) == schedule


async def test_rescheduling_replaces_every_schedule_column(sessions):
    """RM-WRITE-009 — tests/brd/reminders.feature"""
    async with sessions() as session:
        reminder = await create_reminder(
            session,
            instruction="Ping.",
            schedule=resolve(interval_minutes=120, quiet_windows=["22:00-09:00"], now=NOW, tz=TZ),
            tz=TZ,
        )
        await session.commit()
        weekly = resolve(days=["Mon"], clock="08:30", now=NOW, tz=TZ)
        await reschedule_reminder(session, reminder.id, schedule=weekly, tz=TZ)
        await session.commit()

    async with sessions() as session:
        stored = schedule_of(await session.get(Reminder, reminder.id))
        assert stored.kind is ScheduleKind.WEEKLY
        assert stored.interval_minutes is None
        assert stored.quiet_windows == ()


async def test_deleting_a_reminder_removes_the_row(sessions):
    """RM-WRITE-010 — tests/brd/reminders.feature"""
    async with sessions() as session:
        reminder = await create_reminder(
            session,
            instruction="Ping.",
            schedule=resolve(interval_minutes=120, now=NOW, tz=TZ),
            tz=TZ,
        )
        await session.commit()
        await delete_reminder(session, reminder.id)
        await session.commit()
        assert await session.get(Reminder, reminder.id) is None


async def test_reminder_mutations_bump_the_workspace_revision(sessions):
    async with sessions() as session:
        before = (await session.get(Workspace, 1)).revision
        await create_reminder(
            session,
            instruction="Ping.",
            schedule=resolve(interval_minutes=120, now=NOW, tz=TZ),
            tz=TZ,
        )
        await session.commit()
        assert (await session.get(Workspace, 1)).revision > before


# --- proposal payload round trip ------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"interval_minutes": 120, "quiet_windows": ["22:00-09:00"]},
        {"days": ["Mon", "Wed"], "clock": "08:30"},
        {"clock": "09:00", "day": "20.08.2026"},
    ],
)
def test_a_schedule_survives_the_proposal_json_round_trip(kwargs):
    """RM-WRITE-008 — tests/brd/reminders.feature"""
    # The resolved schedule travels through the proposal, not the words the model used.
    schedule = resolve(now=NOW, tz=TZ, **kwargs)
    assert schedule_from_payload(schedule_payload(schedule)) == schedule


def test_the_proposal_payload_is_json_serializable():
    """RM-WRITE-008 — tests/brd/reminders.feature"""
    import json

    schedule = resolve(interval_minutes=120, clock="09:00", day="20.08.2026", now=NOW, tz=TZ)
    assert json.loads(json.dumps(schedule_payload(schedule)))


# --- startup reconciliation -----------------------------------------------


async def _add(sessions, *, due, **kwargs) -> int:
    schedule = resolve(now=NOW, tz=TZ, **kwargs)
    async with sessions() as session:
        reminder = Reminder(
            instruction="Ping.", next_fire_at=due, **schedule_columns(schedule)
        )
        session.add(reminder)
        await session.commit()
        return reminder.id


async def test_reconcile_leaves_an_in_grace_overdue_repeat_for_the_poll(sessions):
    """RM-START-020 — tests/brd/reminders.feature"""
    # The first poll firing it IS the catch-up, so boot must not steal it.
    due = NOW - timedelta(minutes=REMINDER_CATCHUP_GRACE_MINUTES - 5)
    reminder_id = await _add(sessions, due=due, interval_minutes=120)
    async with sessions() as session:
        await reconcile_reminders(session, now=NOW)
        await session.commit()
        assert (await session.get(Reminder, reminder_id)).next_fire_at == due


async def test_reconcile_rolls_a_long_overdue_repeat_forward(sessions):
    """RM-START-020 — tests/brd/reminders.feature"""
    reminder_id = await _add(sessions, due=NOW - timedelta(days=3), interval_minutes=120)
    async with sessions() as session:
        await reconcile_reminders(session, now=NOW)
        await session.commit()
        assert (await session.get(Reminder, reminder_id)).next_fire_at > NOW


async def test_reconcile_never_moves_a_one_shot(sessions):
    """RM-START-020 — tests/brd/reminders.feature"""
    # A one-shot always fires, however late; that is its whole contract.
    due = NOW - timedelta(days=30)
    reminder_id = await _add(sessions, due=due, clock="09:00", day="20.08.2026")
    async with sessions() as session:
        await reconcile_reminders(session, now=NOW)
        await session.commit()
        assert (await session.get(Reminder, reminder_id)).next_fire_at == due


async def test_reconcile_rebuilds_a_wall_clock_after_a_timezone_move(sessions):
    """RM-START-020 — tests/brd/reminders.feature"""
    reminder_id = await _add(
        sessions, due=NOW + timedelta(days=1), days=["Mon", "Wed"], clock="08:30"
    )
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        workspace.timezone = "Europe/Lisbon"  # UTC+1 in August, was UTC+3
        await session.commit()
        await reconcile_reminders(session, now=NOW)
        await session.commit()

    async with sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        local = reminder.next_fire_at.astimezone(ZoneInfo("Europe/Lisbon"))
        assert local.strftime("%H:%M") == "08:30"
        assert local.strftime("%a") in {"Mon", "Wed"}


async def test_reconcile_is_a_no_op_when_the_timezone_has_not_moved(sessions):
    """RM-START-020 — tests/brd/reminders.feature"""
    async with sessions() as session:
        reminder = await create_reminder(
            session,
            instruction="Ping.",
            schedule=resolve(days=["Mon", "Wed"], clock="08:30", now=NOW, tz=TZ),
            tz=TZ,
        )
        await session.commit()
        before = reminder.next_fire_at
        await reconcile_reminders(session, now=datetime.now(UTC))
        await session.commit()
        assert (await session.get(Reminder, reminder.id)).next_fire_at == before


# --- the guard ------------------------------------------------------------


def test_a_background_lease_is_marked_background():
    """RM-GATE-018 — tests/brd/reminders.feature"""
    guard = GenerationGuard()
    assert guard.reserve_background() is True
    assert guard.active and guard.background


def test_an_owner_lease_is_not_background():
    """RM-GATE-018 — tests/brd/reminders.feature"""
    guard = GenerationGuard()
    guard.reserve(101)
    assert guard.active and not guard.background


def test_a_background_lease_never_steals_from_the_owner():
    """RM-GATE-018 — tests/brd/reminders.feature"""
    guard = GenerationGuard()
    guard.reserve(101)
    assert guard.reserve_background() is False
    assert guard.active_source_id == 101


def test_releasing_a_background_lease_frees_the_guard():
    """RM-GATE-018 — tests/brd/reminders.feature"""
    guard = GenerationGuard()
    guard.reserve_background()
    guard.release(BACKGROUND_SOURCE_ID)
    assert not guard.active and not guard.background


def test_cancelling_a_background_lease_bumps_the_dialogue_revision():
    """RM-GATE-018 — tests/brd/reminders.feature"""
    # That bump is how the running turn learns it lost and must discard its answer.
    guard = GenerationGuard()
    guard.reserve_background()
    revision = guard.dialogue_revision
    guard.cancel()
    assert guard.dialogue_revision != revision
    assert not guard.active
    assert guard.reserve(101) is True  # the owner can take it immediately


def test_releasing_a_background_lease_cannot_free_an_owner_lease():
    """RM-GATE-018 — tests/brd/reminders.feature"""
    guard = GenerationGuard()
    guard.reserve_background()
    guard.cancel()
    guard.reserve(101)
    guard.release(BACKGROUND_SOURCE_ID)
    assert guard.active_source_id == 101


# --- cue text ------------------------------------------------------


def _firing(**kwargs) -> Firing:
    defaults = dict(
        reminder_id=7,
        instruction="Ask me what to start with today.",
        schedule="every weekday at 08:30",
        due_at=NOW,
    )
    return Firing(**{**defaults, **kwargs})


def test_one_firing_reads_as_one():
    """RM-FIRE-011 — tests/brd/reminders.feature"""
    text = format_cue([_firing()], now=NOW)
    assert text.startswith("1 Reminder triggered.")
    assert "Reminder #7" in text
    assert "Ask me what to start with today." in text


def test_a_batch_is_numbered():
    """RM-FIRE-011 — tests/brd/reminders.feature"""
    text = format_cue(
        [_firing(reminder_id=7), _firing(reminder_id=12), _firing(reminder_id=19)],
        now=NOW,
    )
    assert text.startswith("3 Reminders triggered.")
    assert "1. Reminder #7" in text
    assert "3. Reminder #19" in text


def test_the_main_advisor_is_told_to_verify_named_items_first():
    """RM-FIRE-011 — tests/brd/reminders.feature"""
    text = format_cue([_firing(instruction="Review Card #88.")], now=NOW)
    assert "check their current state with query_safwa" in text
    assert "it may no longer apply" in text


def test_a_late_firing_says_how_late():
    """RM-FIRE-016 — tests/brd/reminders.feature"""
    text = format_cue(
        [_firing(due_at=NOW - timedelta(hours=4))], now=NOW
    )
    assert "Was due 4 hours ago." in text


def test_an_on_time_firing_says_nothing_about_lateness():
    """RM-FIRE-011 — tests/brd/reminders.feature"""
    assert "Was due" not in format_cue([_firing()], now=NOW)


async def test_reminder_advisor_receives_canonical_dialogue(sessions, monkeypatch):
    """RM-FIRE-011 — tests/brd/reminders.feature"""
    canonical = [
        DialogueMessage(role="user", content="[Initial request]: Build a healthier routine"),
        DialogueMessage(role="assistant", content="Let us begin with sleep."),
    ]

    class History:
        chat_ids: list[int] = []

        async def dialogue(self, chat_id: int) -> list[DialogueMessage]:
            self.chat_ids.append(chat_id)
            return list(canonical)

    class Advisor:
        calls: list[tuple[str, list[DialogueMessage]]] = []

        async def handle(
            self, text: str, *, dialogue: list[DialogueMessage]
        ) -> AIOutcome:
            self.calls.append((text, dialogue))
            return AIOutcome("answer", "Reminder answer")

    history = History()
    advisor = Advisor()
    guard = GenerationGuard()
    services = SimpleNamespace(
        sessions=sessions,
        history=history,
        advisor=advisor,
        guard=guard,
    )
    runtime = CueRuntime(services, object(), owner_id=42)
    rendered: list[tuple[MessageKind, str]] = []

    async def fake_render(_message, _services, _outcome, *, kind, event_id):
        rendered.append((kind, event_id))

    monkeypatch.setattr(
        "safwa.cues.runtime.render_ai_outcome", fake_render
    )
    monkeypatch.setattr(runtime, "_anchor", lambda: object())

    assert await runtime.can_speak() is True
    assert await runtime.speak("a" * 32, format_cue([_firing()], now=NOW)) is True
    runtime.release()

    assert history.chat_ids == [42]
    request, dialogue = advisor.calls[0]
    assert dialogue[:-1] == canonical
    assert dialogue[-1] == DialogueMessage(role="user", content=request)
    assert request.startswith("1 Reminder triggered.")
    assert rendered == [(MessageKind.CUE, "a" * 32)]


async def test_a_registered_cue_event_is_not_generated_twice(sessions):
    """RM-FIRE-013 — tests/brd/reminders.feature"""
    event_id = "b" * 32
    async with sessions() as session:
        session.add(
            TelegramMessage(
                chat_id=42,
                message_id=73,
                event_id=event_id,
                direction="out",
                kind=MessageKind.CUE.value,
            )
        )
        await session.commit()

    runtime = _gate_runtime(sessions)
    assert await runtime.can_speak() is True
    assert await runtime.speak(event_id, "Do not say this twice.") is True
    runtime.release()


async def test_a_cue_render_failure_releases_its_pending_proposal(sessions, monkeypatch):
    """RM-FIRE-013 — tests/brd/reminders.feature"""
    cancelled: list[tuple[str, int]] = []

    class History:
        async def dialogue(self, _chat_id: int) -> list[DialogueMessage]:
            return []

    class Advisor:
        async def handle(
            self, _text: str, *, dialogue: list[DialogueMessage]
        ) -> AIOutcome:
            return AIOutcome("proposal", "Review this", proposal_id=17)

        async def cancel_approval_for_target(self, target_type: str, target_id: int) -> None:
            cancelled.append((target_type, target_id))

    async def failed_render(*_args, **_kwargs):
        raise RuntimeError("Telegram unavailable")

    services = SimpleNamespace(
        sessions=sessions,
        history=History(),
        advisor=Advisor(),
        guard=GenerationGuard(),
    )
    runtime = CueRuntime(services, object(), owner_id=42)
    monkeypatch.setattr("safwa.cues.runtime.render_ai_outcome", failed_render)
    monkeypatch.setattr(runtime, "_anchor", lambda: object())

    assert await runtime.can_speak() is True
    assert await runtime.speak("c" * 32, "Try me again.") is False
    runtime.release()

    assert cancelled == [("proposal", 17)]


async def test_cancelling_a_foreground_lease_aborts_its_task() -> None:
    """RM-GATE-018 — tests/brd/reminders.feature"""
    # Bumping the revision only marks the answer stale; the provider calls must stop.
    guard = GenerationGuard()
    started = asyncio.Event()
    finished = False

    async def generation() -> None:
        nonlocal finished
        guard.reserve(101)
        started.set()
        await asyncio.sleep(30)
        finished = True

    task = asyncio.create_task(generation())
    await started.wait()

    guard.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    assert finished is False
    assert not guard.active


async def test_cancelling_a_background_lease_leaves_its_loop_running() -> None:
    """RM-GATE-018 — tests/brd/reminders.feature"""
    # A background holder's task is a long-lived loop; cancelling it would end the loop.
    guard = GenerationGuard()
    started = asyncio.Event()

    async def loop() -> None:
        guard.reserve_background()
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(loop())
    await started.wait()

    guard.cancel()
    await asyncio.sleep(0)

    assert not task.cancelled()
    task.cancel()


# --- the gate -------------------------------------------------------------


def _gate_runtime(sessions) -> CueRuntime:
    services = SimpleNamespace(
        sessions=sessions,
        history=None,
        advisor=None,
        guard=GenerationGuard(),
    )
    return CueRuntime(services, object(), owner_id=42)


async def test_an_open_question_of_any_shape_closes_the_gate(sessions):
    """RM-GATE-017 — tests/brd/reminders.feature"""
    runtime = _gate_runtime(sessions)
    assert await runtime.can_speak() is True
    runtime.release()

    runtime.services.guard.reserve(7)
    assert await runtime.can_speak() is False
    runtime.services.guard.release(7)

    async with sessions() as session:
        proposal = ChangeProposal(
            message="Create Reminder", status=ProposalStatus.PENDING.value, workspace_revision=1
        )
        session.add(proposal)
        await session.commit()
    assert await runtime.can_speak() is False
    async with sessions() as session:
        (await session.get(ChangeProposal, proposal.id)).status = (
            ProposalStatus.APPROVED.value
        )
        await session.commit()

    async with sessions() as session:
        run = AgentRun(status="running", provider="scripted", model="test")
        session.add(run)
        await session.flush()
        session.add(
            AgentStep(
                run_id=run.id,
                position=1,
                kind="approval_batch",
                metadata_json={"status": "pending"},
            )
        )
        await session.commit()
    assert await runtime.can_speak() is False
    async with sessions() as session:
        step = await session.scalar(select(AgentStep))
        step.metadata_json = {"status": "resolved"}
        await session.commit()

    async with sessions() as session:
        (await session.get(AgentRun, run.id)).claimed_at = datetime.now(UTC)
        await session.commit()
    assert await runtime.can_speak() is False


# --- Safwa's own Reminders ------------------------------------------------


async def test_every_write_path_refuses_a_reminder_safwa_derived(sessions):
    """RM-SYSTEM-022 — tests/brd/reminders.feature"""
    schedule = resolve(interval_minutes=120, now=NOW, tz=TZ)
    async with sessions() as session:
        reminder = await create_reminder(
            session, instruction="Safwa's own.", schedule=schedule, tz=TZ
        )
        reminder.system = True
        await session.commit()
        reminder_id = reminder.id

    async with sessions() as session:
        for attempt in (
            update_reminder_text(session, reminder_id, "Mine now."),
            reschedule_reminder(session, reminder_id, schedule=schedule, tz=TZ),
            delete_reminder(session, reminder_id),
        ):
            with pytest.raises(DomainError, match="change it in Settings"):
                await attempt
        assert await session.get(Reminder, reminder_id) is not None


async def test_no_reminder_reaches_the_cacheable_board_context(sessions):
    """RM-READ-024 — tests/brd/reminders.feature"""
    # `next_fire_at` moves on every fire, and this block is the prefix a remote provider
    # caches; the model reads Reminders through `ai_reminders` instead.
    async with sessions() as session:
        await create_reminder(
            session,
            instruction="Review the launch plan — Card #88.",
            schedule=resolve(interval_minutes=120, now=NOW, tz=TZ),
            tz=TZ,
        )
        await session.commit()
        context = await board_context(session)

    assert "Review the launch plan" not in context.state
    assert "Reminder" not in context.state
