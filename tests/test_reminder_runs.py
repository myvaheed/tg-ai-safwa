"""Reminders that come due on a run of a Check's answers, and the forms an unclear one gets."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from schedule_helpers import create_check
from ui_harness import FakeMessage, button_texts, services_for

from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import CHECK_ANSWERED, resolve_check
from safwa.features.reminders.agent import REMINDER_CHECK_FORMS, REMINDER_TIME_FORMS, unclear
from safwa.features.reminders.api import (
    Schedule,
    describe,
    next_fire,
    resolve,
    schedule_from_payload,
    schedule_payload,
)
from safwa.features.reminders.firing import tick
from safwa.features.reminders.hooks import REMINDER_RUN_HOOK
from safwa.features.reminders.model import Reminder, ScheduleKind
from safwa.features.reminders.telegram import render_reminder, render_reminders
from safwa.features.reminders.telegram.review import ReminderProposalPresenter
from safwa.features.reminders.use_cases import create_reminder, reconcile_reminders
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.queue import waiting_cues
from tg_agent_shell.hooks.contracts import OnCommitted, Run
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.api import ChangeAction, Effect, ProposalChange
from tg_agent_shell.proposals.marks import field_line

TZ = ZoneInfo("Europe/Istanbul")


def run(series_id: int, outcome: str = "missed", count: int = 3) -> Schedule:
    return Schedule(
        kind=ScheduleKind.RUN, run_series_id=series_id, run_outcome=outcome, run_length=count
    )


async def run_reminder(session, series_id: int, **kwargs) -> Reminder:
    return await create_reminder(
        session, instruction="Cheer the owner on.", schedule=run(series_id, **kwargs), tz=TZ
    )


def test_rm_run_025_a_run_reads_as_its_check_answer_and_count():
    """RM-RUN-025 — tests/brd/reminders.feature"""
    assert describe(run(12), tz=TZ) == "each time Check #12 is Missed 3 times in a row"
    assert describe(run(12, "passed", 40), tz=TZ) == "each time Check #12 is Passed 40 times in a row"
    assert describe(run(12, count=1), tz=TZ) == "each time Check #12 is Missed"
    # No clock ever fires it, and it travels through a proposal whole.
    assert next_fire(run(12), previous=None, now=datetime.now(UTC), tz=TZ) is None
    assert schedule_from_payload(schedule_payload(run(12))) == run(12)


async def test_rm_run_025_a_run_reminder_has_no_time_the_clock_or_a_restart_would_move(sessions):
    """RM-RUN-025 — tests/brd/reminders.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        reminder = await run_reminder(session, check.id)
        await session.commit()
        reminder_id = reminder.id

    assert await tick(sessions, tz=TZ) is False
    async with sessions() as session:
        await reconcile_reminders(session)
        await session.commit()
        stored = await session.get(Reminder, reminder_id)
        assert stored.next_fire_at is None
        assert stored.schedule_kind == ScheduleKind.RUN.value
        assert await waiting_cues(session) == []


async def test_rm_run_025_the_review_screen_shows_the_run_and_no_first_firing(sessions):
    """RM-RUN-025 — tests/brd/reminders.feature"""
    async with sessions() as session:
        screen = await ReminderProposalPresenter().screen(
            session,
            [
                ProposalChange(
                    entity="reminder",
                    action=ChangeAction.CREATE,
                    values={
                        "instruction": "Cheer the owner on.",
                        "schedule": schedule_payload(run(12)),
                        "schedule_text": describe(run(12), tz=TZ),
                    },
                )
            ],
        )

    lines = [field_line(field, Effect.CREATE) for field in screen.fields]
    assert lines == [
        "🔁 When: each time Check #12 is Missed 3 times in a row",
        None,
        "Words: Cheer the owner on.",
    ]


async def test_rm_run_026_a_run_reminder_fires_at_every_multiple_of_its_count(sessions):
    """RM-RUN-026 — tests/brd/reminders.feature"""
    assert REMINDER_RUN_HOOK.on == (OnCommitted(kind=CHECK_ANSWERED),)
    assert isinstance(REMINDER_RUN_HOOK.effect, Run)
    # A Run is not in Profile → Hooks: deleting the Reminder is its only off switch.
    assert not REMINDER_RUN_HOOK.agent_related
    sink = bind_committed(
        sessions, HookRegistry.of((REMINDER_RUN_HOOK,), owners=frozenset({"reminders"}))
    )
    async with sessions() as session:
        live = await create_check(session, title="Sat straight?", schedule="after completion")
        await run_reminder(session, live.id)
        await session.commit()
    await sink.drain()

    async def answer(outcome: CheckOutcome) -> list[str]:
        nonlocal live
        async with sessions() as session:
            _, live = await resolve_check(session, live.id, outcome)
            await session.commit()
        await sink.drain()
        async with sessions() as session:
            return [cue.text for cue in await waiting_cues(session)]

    assert await answer(CheckOutcome.MISSED) == []
    assert await answer(CheckOutcome.MISSED) == []
    (said,) = await answer(CheckOutcome.MISSED)
    assert "Text: Cheer the owner on." in said
    assert "each time Check #" in said and "Missed 3 times in a row; 3 in a row now" in said
    # The Pending instance on top neither counted nor broke the run; a fourth and a fifth say
    # nothing, and the sixth says it again.
    assert await answer(CheckOutcome.MISSED) == [said]
    assert await answer(CheckOutcome.MISSED) == [said]
    assert (await answer(CheckOutcome.MISSED))[-1].endswith("6 in a row now")
    # A Passed answer starts a run of Passed, which a Reminder on Missed does not count.
    assert len(await answer(CheckOutcome.PASSED)) == 2


async def test_rm_run_026_a_reminder_on_passed_counts_passed_on_its_whole_series(sessions):
    """RM-RUN-026 — tests/brd/reminders.feature"""
    sink = bind_committed(
        sessions, HookRegistry.of((REMINDER_RUN_HOOK,), owners=frozenset({"reminders"}))
    )
    async with sessions() as session:
        live = await create_check(session, title="Slept 7 hours?", schedule="after completion")
        first = live.id
        await run_reminder(session, first, outcome="passed", count=2)
        await session.commit()
    for outcome in (CheckOutcome.MISSED, CheckOutcome.PASSED, CheckOutcome.PASSED):
        async with sessions() as session:
            _, live = await resolve_check(session, live.id, outcome)
            await session.commit()
        await sink.drain()

    async with sessions() as session:
        (cue,) = await waiting_cues(session)
    assert live.id != first  # answered on later instances of the series it was set on
    assert f"each time Check #{first} is Passed 2 times in a row; 2 in a row now" in cue.text


async def test_rm_run_027_a_run_reminder_is_listed_and_opened_like_any_other(sessions):
    """RM-RUN-027 — tests/brd/reminders.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        reminder = await run_reminder(session, check.id)
        await create_reminder(
            session,
            instruction="Take a walk.",
            schedule=resolve(interval_minutes=120, now=datetime.now(UTC), tz=TZ),
            tz=TZ,
        )
        await session.commit()
        check_id, reminder_id = check.id, reminder.id
    services = services_for(sessions)

    listing = FakeMessage(70, bot_message=True)
    await render_reminders(listing, services)
    assert button_texts(listing.edits[-1][1])[:2] == [
        "Take a walk. · every 2 hours",
        f"Cheer the owner on. · each time Check #{check_id} is Missed 3 times in a row",
    ]

    detail = FakeMessage(71, bot_message=True)
    await render_reminder(detail, services, reminder_id)
    text, markup = detail.edits[-1]
    assert "is Missed 3 times in a row" in text and "next " not in text
    assert button_texts(markup)[:2] == ["✏️ Text", "🗑 Delete"]


async def test_rm_run_027_safwa_reads_a_run_reminder_in_ai_reminders(read_views):
    """RM-RUN-027 — tests/brd/reminders.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        await run_reminder(session, check.id)
        await session.commit()
        check_id = check.id

    rows = (
        await runner.run(
            "SELECT schedule_kind, run_series_id, run_outcome, run_length, next_fire_at_local "
            "FROM ai_reminders"
        )
    ).rows
    assert rows == [
        {
            "schedule_kind": "run",
            "run_series_id": check_id,
            "run_outcome": "missed",
            "run_length": 3,
            "next_fire_at_local": None,
        }
    ]


def test_rm_unclear_028_the_forms_follow_what_the_words_meant():
    """RM-UNCLEAR-028 — tests/brd/reminders.feature"""
    assert REMINDER_TIME_FORMS == (
        "tomorrow at 15:00",
        "on Thursday at 10:00",
        "every weekday at 8:00",
        "every 2 hours except 22:00–09:00",
    )
    assert REMINDER_CHECK_FORMS == (
        "every 3 Missed in a row of Check #12",
        "after 40 Passed in a row of Check #12",
        "each time Check #12 is Missed",
    )

    def said(about):
        return str(unclear("No hour was given.", about))

    assert said("time").splitlines()[0] == "No hour was given."
    assert all(form in said("time") for form in REMINDER_TIME_FORMS)
    assert not any(form in said("time") for form in REMINDER_CHECK_FORMS)
    assert all(form in said("check") for form in REMINDER_CHECK_FORMS)
    assert not any(form in said("check") for form in REMINDER_TIME_FORMS)
    assert all(form in said(None) for form in REMINDER_TIME_FORMS + REMINDER_CHECK_FORMS)
