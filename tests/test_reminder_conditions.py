"""Reminders that come due on answers of Checks or Actions, and the forms an unclear one gets."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from schedule_helpers import create_card, create_check
from sqlalchemy import text
from ui_harness import FakeMessage, button_texts, services_for

from safwa.features.cards.use_cases import CARD_DONE, delete_card, finish_action, toggle_card_check
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import CHECK_ANSWERED, resolve_check
from safwa.features.reminders.agent import (
    REMINDER_ANSWER_FORMS,
    REMINDER_ITEMS_PROMPT,
    REMINDER_TIME_FORMS,
    ConditionInput,
    unclear,
)
from safwa.features.reminders.api import (
    Condition,
    Schedule,
    describe,
    next_fire,
    resolve,
    schedule_from_payload,
    schedule_payload,
)
from safwa.features.reminders.conditions import (
    CONDITION_PREVIEW_TITLES,
    ConditionError,
    checked_select,
    finds_text,
    found_ids,
    item_names,
    most_answers,
)
from safwa.features.reminders.firing import tick
from safwa.features.reminders.hooks import REMINDER_CONDITION_HOOK
from safwa.features.reminders.model import Reminder, ScheduleKind
from safwa.features.reminders.telegram import render_reminder, render_reminders
from safwa.features.reminders.telegram.review import ReminderProposalPresenter
from safwa.features.reminders.use_cases import create_reminder, reconcile_reminders
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.queue import waiting_cues
from tg_agent_shell.hooks.contracts import OnCommitted, Run
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.api import ChangeAction, Effect, ProposalChange
from tg_agent_shell.proposals.marks import field_line

TZ = ZoneInfo("Europe/Istanbul")
SPORT_SQL = (
    "SELECT id FROM ai_cards WHERE kind = 'action' "
    "AND instr(',' || COALESCE(direct_tags, '') || ',', ',sport,') > 0"
)


def of_check(check_id: int, answer: str = "missed", count: int = 3, *, in_row: bool = True):
    return Condition(
        items=f"Check #{check_id}",
        sql=(
            "SELECT id FROM ai_checks WHERE series_id = "
            f"(SELECT series_id FROM ai_checks WHERE id = {check_id})"
        ),
        answer=answer,
        count=count,
        in_row=in_row,
    )


def on(condition: Condition) -> Schedule:
    return Schedule(kind=ScheduleKind.CONDITION, condition=condition)


async def condition_reminder(session, condition: Condition) -> Reminder:
    return await create_reminder(
        session, instruction="Cheer the owner on.", schedule=on(condition), tz=TZ
    )


def hook_sink(sessions):
    return bind_committed(
        sessions, HookRegistry.of((REMINDER_CONDITION_HOOK,), owners=frozenset({"reminders"}))
    )


async def said(sessions) -> list[str]:
    async with sessions() as session:
        return [cue.text for cue in await waiting_cues(session)]


def test_a_condition_reads_as_its_items_answer_and_count():
    """RM-COND-030 — tests/brd/reminders.feature"""
    assert describe(on(of_check(12)), tz=TZ) == "every 3 Missed in a row of Check #12"
    assert describe(on(of_check(12, "passed", 40)), tz=TZ) == "every 40 Passed in a row of Check #12"
    assert (
        describe(on(of_check(12, "answered", 30, in_row=False)), tz=TZ)
        == "every 30 answers of Check #12"
    )
    assert describe(on(of_check(12, count=1)), tz=TZ) == "each Missed of Check #12"
    sport = Condition(items="Actions tagged sport", sql=SPORT_SQL, answer="done", count=10)
    assert describe(on(sport), tz=TZ) == "every 10 Done of Actions tagged sport"
    # No clock ever fires it, and it travels through a proposal whole.
    assert next_fire(on(sport), previous=None, now=datetime.now(UTC), tz=TZ) is None
    assert schedule_from_payload(schedule_payload(on(sport))) == on(sport)


def test_only_passed_and_missed_count_in_a_row():
    """RM-COND-030 — tests/brd/reminders.feature"""
    ConditionInput(items="Check #12", answer="missed", count=3, in_row=True)
    for answer in ("answered", "done"):
        with pytest.raises(ValidationError, match="in_row needs answer passed or missed"):
            ConditionInput(items="Check #12", answer=answer, count=3, in_row=True)


async def test_a_condition_has_no_time_the_clock_or_a_restart_would_move(sessions):
    """RM-COND-030 — tests/brd/reminders.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        reminder = await condition_reminder(session, of_check(check.id))
        await session.commit()
        reminder_id = reminder.id

    assert await tick(sessions, tz=TZ) is False
    async with sessions() as session:
        await reconcile_reminders(session)
        await session.commit()
        stored = await session.get(Reminder, reminder_id)
        assert stored.next_fire_at is None
        assert stored.schedule_kind == ScheduleKind.CONDITION.value
        assert await waiting_cues(session) == []


async def test_the_review_screen_shows_what_the_select_finds_and_no_first_firing(sessions):
    """RM-COND-030 — tests/brd/reminders.feature"""
    async with sessions() as session:
        titles = ["Sat straight?", "Slept 7 hours?", "Drank water?", "Read?", "Walked?"]
        checks = [await create_check(session, title=title) for title in titles]
        _, successor = await resolve_check(
            session,
            (await create_check(session, title="Ran?", schedule="after completion")).id,
            CheckOutcome.PASSED,
        )
        condition = Condition(items="any Check", sql="SELECT id FROM ai_checks", answer="missed", count=3)
        ids = await found_ids(session, condition)
        # A series is named once, by its newest title.
        assert await finds_text(session, condition, ids) == (
            "«Ran?», «Walked?», «Read?» and 3 more."
        )
        assert CONDITION_PREVIEW_TITLES == 3
        single = of_check(checks[0].id)
        finds = await finds_text(session, single, await found_ids(session, single))
        screen = await ReminderProposalPresenter().screen(
            session,
            [
                ProposalChange(
                    entity="reminder",
                    action=ChangeAction.CREATE,
                    values={
                        "instruction": "Cheer the owner on.",
                        "schedule": schedule_payload(on(single)),
                        "schedule_text": describe(on(single), tz=TZ),
                        "finds": finds,
                    },
                )
            ],
        )
        empty = Condition(items="Actions tagged sport", sql=SPORT_SQL, answer="done", count=10)
        assert await finds_text(session, empty, await found_ids(session, empty)) == "Nothing yet."
    assert successor is not None
    lines = [field_line(field, Effect.CREATE) for field in screen.fields]
    assert lines == [
        f"🔁 When: every 3 Missed in a row of Check #{checks[0].id}",
        "🔎 Finds now: «Sat straight?».",
        "Words: Cheer the owner on.",
    ]


@pytest.mark.parametrize(
    ("sql", "answer", "error"),
    [
        ("DELETE FROM checks", "missed", "Only SELECT queries are allowed"),
        ("SELECT id FROM ai_checks; SELECT 1", "missed", "Only one SQL statement is allowed"),
        ("SELECT id FROM ai_reminders", "missed", "ai_reminders"),
        ("SELECT id FROM ai_cards", "missed", "The SELECT must read ai_checks"),
        ("SELECT id FROM ai_checks", "done", "The SELECT must read ai_cards"),
    ],
)
def test_a_select_that_cannot_find_the_items_is_refused_as_text(sql, answer, error):
    """RM-COND-031 — tests/brd/reminders.feature"""
    with pytest.raises(ConditionError, match=error):
        checked_select(sql, answer)


async def test_the_select_session_spells_tags_and_values_as_stored(sessions):
    """RM-COND-030 — tests/brd/reminders.feature"""
    assert "Spell a Tag or a Value exactly as the context lists it." in REMINDER_ITEMS_PROMPT
    async with sessions() as session:
        assert await item_names(session) == "Tags: none\nValues: none"
        sport = await create_tag(session, "Sport")
        await create_tag(session, "reading")
        await create_value(session, "Health", "Strength for the years ahead.")
        await create_card(session, kind="action", title="Run", stage="today", tag_ids={sport.id})
        assert await item_names(session) == "Tags: reading, Sport\nValues: Health"
        # Every example and every column the prompt names runs on the views as written.
        for line in REMINDER_ITEMS_PROMPT.splitlines():
            if line.startswith("'"):
                sql = line[line.index(": SELECT") + 2 :]
                answer = "done" if "FROM ai_cards" in sql.split("WHERE")[0] else "missed"
                condition = Condition(items="", sql=sql, answer=answer, count=1)
                await found_ids(session, condition)
            if line.startswith(("ai_checks(", "ai_cards(")):
                view, _, columns = line.rstrip(")").partition("(")
                await session.execute(text(f"SELECT {columns} FROM {view} LIMIT 0"))
        tagged = Condition(
            items="Actions tagged sport",
            sql=SPORT_SQL.replace(",sport,", ",Sport,"),
            answer="done",
            count=1,
        )
        assert len(await found_ids(session, tagged)) == 1


async def test_a_select_that_does_not_return_ids_is_refused_when_it_runs(sessions):
    """RM-COND-031 — tests/brd/reminders.feature"""
    async with sessions() as session:
        for sql, error in (
            ("SELECT title FROM ai_checks", "a column named id"),
            ("SELECT id FROM ai_checks WHERE nonsense = 1", "does not run"),
        ):
            condition = Condition(items="any Check", sql=sql, answer="missed", count=3)
            with pytest.raises(ConditionError, match=error):
                await found_ids(session, condition)


async def test_a_condition_fires_at_every_multiple_of_its_count(sessions):
    """RM-COND-032 — tests/brd/reminders.feature"""
    assert REMINDER_CONDITION_HOOK.on == (
        OnCommitted(kind=CHECK_ANSWERED),
        OnCommitted(kind=CARD_DONE),
    )
    assert isinstance(REMINDER_CONDITION_HOOK.effect, Run)
    # A Run is not in Profile → Hooks: deleting the Reminder is its only off switch.
    assert not REMINDER_CONDITION_HOOK.agent_related
    sink = hook_sink(sessions)
    async with sessions() as session:
        live = await create_check(session, title="Sat straight?", schedule="after completion")
        await condition_reminder(session, of_check(live.id))
        await session.commit()
    await sink.drain()

    async def answer(outcome: CheckOutcome) -> list[str]:
        nonlocal live
        async with sessions() as session:
            _, live = await resolve_check(session, live.id, outcome)
            await session.commit()
        await sink.drain()
        return await said(sessions)

    assert await answer(CheckOutcome.MISSED) == []
    assert await answer(CheckOutcome.MISSED) == []
    (first,) = await answer(CheckOutcome.MISSED)
    assert "Text: Cheer the owner on." in first
    assert "in a row of Check #" in first
    assert "; 3 in a row now; 0 Passed and 3 Missed in all" in first
    # The Pending instance on top neither counted nor broke the run; a fourth and a fifth say
    # nothing, and the sixth says it again.
    assert await answer(CheckOutcome.MISSED) == [first]
    assert await answer(CheckOutcome.MISSED) == [first]
    assert (await answer(CheckOutcome.MISSED))[-1].endswith(
        "6 in a row now; 0 Passed and 6 Missed in all"
    )
    # A Passed answer is not one a Reminder on Missed counts.
    assert len(await answer(CheckOutcome.PASSED)) == 2


async def test_any_answer_counts_in_all_across_the_series(sessions):
    """RM-COND-032 — tests/brd/reminders.feature"""
    sink = hook_sink(sessions)
    async with sessions() as session:
        live = await create_check(session, title="Slept 7 hours?", schedule="after completion")
        await condition_reminder(session, of_check(live.id, "answered", 2, in_row=False))
        await session.commit()
    for outcome in (CheckOutcome.MISSED, CheckOutcome.PASSED, CheckOutcome.PASSED):
        async with sessions() as session:
            _, live = await resolve_check(session, live.id, outcome)
            await session.commit()
        await sink.drain()

    (cue,) = await said(sessions)
    assert "every 2 answers of Check #" in cue
    assert cue.endswith("2 now; 1 Passed and 1 Missed in all")


async def test_a_corrected_answer_says_nothing(sessions):
    """RM-COND-032 — tests/brd/reminders.feature"""
    sink = hook_sink(sessions)
    async with sessions() as session:
        live = await create_check(session, title="Slept 7 hours?", schedule="after completion")
        first_id = live.id
        await condition_reminder(session, of_check(live.id, "answered", 2, in_row=False))
        await condition_reminder(session, of_check(live.id, "passed", 2))
        await session.commit()
    for outcome in (CheckOutcome.MISSED, CheckOutcome.PASSED):
        async with sessions() as session:
            _, live = await resolve_check(session, live.id, outcome)
            await session.commit()
        await sink.drain()
    assert len(await said(sessions)) == 1

    # Corrected to Passed, the first answer leaves 2 answers and 2 Passed in a row.
    async with sessions() as session:
        await resolve_check(session, first_id, CheckOutcome.PASSED)
        await session.commit()
    await sink.drain()
    assert len(await said(sessions)) == 1


async def test_done_of_actions_counts_in_all(sessions):
    """RM-COND-032 — tests/brd/reminders.feature"""
    sink = hook_sink(sessions)
    async with sessions() as session:
        tag = await create_tag(session, "sport")
        runs = [
            await create_card(
                session, kind="action", title=title, stage="today", tag_ids={tag.id}
            )
            for title in ("Run", "Swim", "Lift")
        ]
        reading = await create_card(session, kind="action", title="Read", stage="today")
        sport = Condition(items="Actions tagged sport", sql=SPORT_SQL, answer="done", count=2)
        await condition_reminder(session, sport)
        await session.commit()

    async def done(card_id: int) -> list[str]:
        async with sessions() as session:
            await finish_action(session, card_id)
            await session.commit()
        await sink.drain()
        return await said(sessions)

    assert await done(runs[0].id) == []
    assert await done(reading.id) == []  # not one the SELECT finds
    (cue,) = await done(runs[1].id)
    assert "every 2 Done of Actions tagged sport; 2 Done now" in cue
    assert await done(runs[2].id) == [cue]


async def test_the_select_runs_again_at_every_answer(sessions):
    """RM-COND-033 — tests/brd/reminders.feature"""
    sink = hook_sink(sessions)
    async with sessions() as session:
        tag = await create_tag(session, "sport")
        sport = Condition(items="Actions tagged sport", sql=SPORT_SQL, answer="done", count=1)
        reminder = await condition_reminder(session, sport)
        await session.commit()
        later = await create_card(
            session, kind="action", title="Run", stage="today", tag_ids={tag.id}
        )
        await session.commit()
    async with sessions() as session:
        await finish_action(session, later.id)
        await session.commit()
    await sink.drain()
    assert len(await said(sessions)) == 1

    async with sessions() as session:
        await delete_card(session, later.id)
        await session.commit()
        stored = await session.get(Reminder, reminder.id)
        assert stored is not None
        assert await found_ids(session, sport) == []


async def test_one_series_runs_as_its_schedule_does(sessions):
    """RM-COND-034 — tests/brd/reminders.feature"""
    async with sessions() as session:
        alone = await create_check(session, title="Slept 7 hours?")
        endless = await create_check(session, title="Sat straight?", schedule="after completion")
        limited = await create_check(
            session,
            title="Stretch?",
            schedule="after completion",
            schedule_rule={"kind": "after_completion", "limit": 5},
        )
        daily = await create_card(session, kind="action", title="Stretch", schedule="daily 09:00")
        on_daily = await create_check(session, title="Felt loose?")
        await toggle_card_check(session, daily.id, on_daily.id)
        once = await create_card(session, kind="action", title="Go to the market")
        on_once = await create_check(session, title="Milk")
        await toggle_card_check(session, once.id, on_once.id)
        await session.commit()

        async def most(condition: Condition) -> int | None:
            return await most_answers(session, condition, await found_ids(session, condition))

        assert await most(of_check(alone.id)) == 1
        assert await most(of_check(endless.id)) is None
        assert await most(of_check(limited.id)) == 5
        assert await most(of_check(on_daily.id)) is None
        assert await most(of_check(on_once.id)) == 1
        # More than one series takes any count.
        every = Condition(items="any Check", sql="SELECT id FROM ai_checks", answer="missed", count=9)
        assert await most(every) is None
        market = Condition(
            items=f"Action #{once.id}",
            sql=f"SELECT id FROM ai_cards WHERE id = {once.id}",
            answer="done",
            count=2,
        )
        assert await most(market) == 1


async def test_a_condition_is_listed_and_opened_like_any_other(sessions):
    """RM-COND-035 — tests/brd/reminders.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        reminder = await condition_reminder(session, of_check(check.id))
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
        f"Cheer the owner on. · every 3 Missed in a row of Check #{check_id}",
    ]

    detail = FakeMessage(71, bot_message=True)
    await render_reminder(detail, services, reminder_id)
    text, markup = detail.edits[-1]
    assert "every 3 Missed in a row" in text and "next " not in text
    assert "🔎 Finds now: «Sat straight?»." in text
    assert button_texts(markup)[:2] == ["✏️ Text", "🗑 Delete"]

    # A SELECT that no longer runs says so on the screen.
    async with sessions() as session:
        stored = await session.get(Reminder, reminder_id)
        stored.condition = {**stored.condition, "sql": "SELECT id FROM ai_checks WHERE gone = 1"}
        await session.commit()
    await render_reminder(detail, services, reminder_id)
    text = detail.edits[-1][0]
    assert "⚠️ It counts nothing: The SELECT does not run: no such column: gone." in text
    assert "Ask Safwa to set it again." in text


async def test_safwa_reads_a_condition_in_ai_reminders(read_views):
    """RM-COND-035 — tests/brd/reminders.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        condition = of_check(check.id)
        await condition_reminder(session, condition)
        await session.commit()

    (row,) = (
        await runner.run(
            "SELECT schedule_kind, json_extract(condition, '$.answer') AS answer, "
            "json_extract(condition, '$.count') AS count, "
            "json_extract(condition, '$.in_row') AS in_row, "
            "json_extract(condition, '$.sql') AS sql, next_fire_at_local FROM ai_reminders"
        )
    ).rows
    assert row == {
        "schedule_kind": "condition",
        "answer": "missed",
        "count": 3,
        "in_row": 1,
        "sql": condition.sql,
        "next_fire_at_local": None,
    }


def test_the_forms_follow_what_the_words_meant():
    """RM-UNCLEAR-028 — tests/brd/reminders.feature"""
    assert REMINDER_TIME_FORMS == (
        "tomorrow at 15:00",
        "on Thursday at 10:00",
        "every weekday at 8:00",
        "every 2 hours except 22:00–09:00",
    )
    assert REMINDER_ANSWER_FORMS == (
        "every 3 Missed in a row of Check #12",
        "every 30 answers of Check #12",
        "every 10 Done of Actions tagged sport",
    )

    def told(about):
        return str(unclear("No hour was given.", about))

    assert told("time").splitlines()[0] == "No hour was given."
    assert all(form in told("time") for form in REMINDER_TIME_FORMS)
    assert not any(form in told("time") for form in REMINDER_ANSWER_FORMS)
    assert all(form in told("answers") for form in REMINDER_ANSWER_FORMS)
    assert not any(form in told("answers") for form in REMINDER_TIME_FORMS)
    assert all(form in told(None) for form in REMINDER_TIME_FORMS + REMINDER_ANSWER_FORMS)
