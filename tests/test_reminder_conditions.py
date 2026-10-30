"""Reminders that come due on answers of Checks or Actions, and the forms an unclear one gets."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from schedule_helpers import create_card, create_check
from ui_harness import FakeMessage, button_texts, services_for

from safwa.features.cards.use_cases import CARD_DONE, delete_card, finish_action, toggle_card_check
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import (
    CHECK_ANSWERED,
    delete_check,
    resolve_check,
    update_check_fields,
)
from safwa.features.reminders.agent import (
    REMINDER_CONDITION_PROMPT,
    REMINDER_TIME_FORMS,
    REMINDER_WHEN_PROMPT,
    ConditionFields,
    ReminderToolInput,
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
    NAME_BY_ID,
    ONE_KIND,
    ConditionError,
    finds_text,
    found_ids,
    items_series,
    never_reached,
)
from safwa.features.reminders.firing import LAST_FIRE, tick
from safwa.features.reminders.hooks import REMINDER_CONDITION_HOOK
from safwa.features.reminders.model import Reminder, ScheduleKind
from safwa.features.reminders.telegram import render_reminder, render_reminders
from safwa.features.reminders.telegram.review import ReminderProposalPresenter
from safwa.features.reminders.use_cases import create_reminder, reconcile_reminders
from safwa.features.tags.use_cases import create_tag
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.queue import waiting_cues
from tg_agent_shell.hooks.contracts import OnCommitted, Run
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.api import ChangeAction, Effect, ProposalChange
from tg_agent_shell.proposals.marks import field_line

TZ = ZoneInfo("Europe/Istanbul")


def of_check(check_id: int, answer: str = "no", count: int = 3, *, in_row: bool = True):
    """A condition on one Check, named by the first copy of its series."""
    return Condition(
        items=f"Check #{check_id}",
        series=(check_id,),
        kind="check",
        answer=answer,
        count=count,
        in_row=in_row,
    )


def of_items(kind: str, ids: list[int], answer: str, count: int, *, in_row: bool = False):
    """A condition on several items of one kind, each the first copy of its series."""
    return Condition(
        items=", ".join(f"{kind.title()} #{item_id}" for item_id in ids),
        series=tuple(ids),
        kind=kind,
        answer=answer,
        count=count,
        in_row=in_row,
    )


def on(condition: Condition) -> Schedule:
    return Schedule(kind=ScheduleKind.CONDITION, condition=condition)


async def condition_reminder(session, condition: Condition) -> Reminder:
    return await create_reminder(
        session, remind_text="Cheer the owner on.", schedule=on(condition), tz=TZ
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
    assert describe(on(of_check(12, "yes", 40)), tz=TZ) == "every 40 Passed in a row of Check #12"
    assert (
        describe(on(of_check(12, "any", 30, in_row=False)), tz=TZ)
        == "every 30 answers of Check #12"
    )
    assert describe(on(of_check(12, count=1)), tz=TZ) == "each Missed of Check #12"
    sport = of_items("action", [3, 5], "yes", 10)
    assert describe(on(sport), tz=TZ) == "every 10 Done of Action #3, Action #5"
    # No clock ever fires it, and it travels through a proposal whole.
    assert next_fire(on(sport), previous=None, now=datetime.now(UTC), tz=TZ) is None
    assert schedule_from_payload(schedule_payload(on(sport))) == on(sport)


def test_after_n_reads_as_once():
    """RM-COND-040 — tests/brd/reminders.feature"""
    once = replace(of_check(12, "yes", 30), once=True)
    assert describe(on(once), tz=TZ) == "after 30 Passed in a row of Check #12"
    assert describe(on(replace(once, count=1, in_row=False)), tz=TZ) == "after 1 Passed of Check #12"
    assert ConditionFields(items="Check #12", answer="yes", count=3).once is False
    assert "count=40, once=true." in REMINDER_CONDITION_PROMPT
    assert schedule_from_payload(schedule_payload(on(once))) == on(once)


async def test_after_n_fires_once_at_n_and_deletes_itself(sessions):
    """RM-COND-040 — tests/brd/reminders.feature"""
    sink = hook_sink(sessions)
    async with sessions() as session:
        live = await create_check(session, title="Meditated?", schedule="after completion")
        after_three = replace(of_check(live.id, "yes"), once=True)
        reminder = await condition_reminder(session, after_three)
        await session.commit()
        reminder_id = reminder.id

    async def answer() -> list[str]:
        nonlocal live
        async with sessions() as session:
            _, live = await resolve_check(session, live.id, CheckOutcome.PASSED)
            await session.commit()
        await sink.drain()
        return await said(sessions)

    assert await answer() == []
    assert await answer() == []
    (cue,) = await answer()
    assert "after 3 Passed in a row of Check #" in cue and "; 3 in a row now;" in cue
    assert LAST_FIRE in cue
    async with sessions() as session:
        assert await session.get(Reminder, reminder_id) is None
    # Gone with its words, it says nothing at the sixth.
    for _ in range(3):
        assert await answer() == [cue]


async def test_after_n_already_past_is_refused_with_the_count_now(sessions):
    """RM-COND-040 — tests/brd/reminders.feature"""
    async with sessions() as session:
        live = await create_check(session, title="Meditated?", schedule="after completion")
        first = live.id
        for _ in range(3):
            _, live = await resolve_check(session, live.id, CheckOutcome.PASSED)
        after_two = replace(of_check(first, "yes", 2), once=True)
        ids = await found_ids(session, after_two)
        assert await never_reached(session, after_two, ids) == (
            f"Check #{first} already has 3 Passed in a row, so 'after 2' is already past."
        )
        assert await never_reached(session, replace(after_two, count=4), ids) is None
        # Every 2 still comes, at the next multiple.
        assert await never_reached(session, replace(after_two, once=False), ids) is None
        assert await finds_text(session, after_two, ids) == "«Meditated?». 3 Passed in a row now."


def test_the_short_session_names_the_answer_only_as_yes_no_or_any():
    """RM-COND-030 — tests/brd/reminders.feature"""
    ConditionFields(items="Check #1, Check #2", answer="no", count=3, in_row=True)
    ConditionFields(items="Action #3", answer="yes", count=50)
    with pytest.raises(ValidationError, match="in_row needs answer yes or no"):
        ConditionFields(items="Check #1", answer="any", count=3, in_row=True)
    # An id alone says neither what it is, and one Reminder counts one kind.
    for items, error in (("#12", NAME_BY_ID), ("#5 и #9", NAME_BY_ID), ("Check #1, Action #2", ONE_KIND)):
        with pytest.raises(ValidationError, match=re.escape(error)):
            ConditionFields(items=items, answer="no", count=3)


def test_a_reminder_is_set_by_a_time_or_by_a_condition():
    """RM-COND-030 — tests/brd/reminders.feature"""
    ReminderToolInput(mode="create", remind_text="Cheer me on.", on_answers="every 3 misses")
    ReminderToolInput(mode="create", remind_text="Cheer me on.", when="every day at 8")
    ReminderToolInput(mode="update", id=4, on_answers="every 3 misses")
    for missing in ({}, {"on_answers": " "}):
        with pytest.raises(ValidationError, match="needs when or on_answers"):
            ReminderToolInput(mode="create", remind_text="Cheer me on.", **missing)
    with pytest.raises(ValidationError, match="not both"):
        ReminderToolInput(
            mode="create",
            remind_text="Cheer me on.",
            when="every day at 8",
            on_answers="every 3 misses",
        )
    # The setup session reads only a time.
    assert "set_condition" not in REMINDER_WHEN_PROMPT


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


async def test_the_review_screen_shows_what_it_counts_and_no_first_firing(sessions):
    """RM-COND-030 — tests/brd/reminders.feature"""
    async with sessions() as session:
        titles = ["Sat straight?", "Slept 7 hours?", "Drank water?", "Read?", "Walked?"]
        checks = [await create_check(session, title=title) for title in titles]
        ran = await create_check(session, title="Ran?", schedule="after completion")
        _, successor = await resolve_check(session, ran.id, CheckOutcome.PASSED)
        every = of_items("check", [check.id for check in checks] + [ran.id], "no", 3)
        ids = await found_ids(session, every)
        # A series is named once, by its newest title.
        assert await finds_text(session, every, ids) == (
            "«Ran?», «Walked?», «Read?» and 3 more. 0 Missed now."
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
                        "remind_text": "Cheer the owner on.",
                        "schedule": schedule_payload(on(single)),
                        "schedule_text": describe(on(single), tz=TZ),
                        "finds": finds,
                    },
                )
            ],
        )
    assert successor is not None
    lines = [field_line(field, Effect.CREATE) for field in screen.fields]
    assert lines == [
        f"<i>🔁 When:</i> every 3 Missed in a row of Check #{checks[0].id}",
        "<i>🔎 Finds now:</i> «Sat straight?». 0 Missed in a row now.",
        "<i>Words:</i> Cheer the owner on.",
    ]


async def test_the_items_are_kept_as_their_series(sessions):
    """RM-COND-030 — tests/brd/reminders.feature"""
    async with sessions() as session:
        live = await create_check(session, title="Sat straight?", schedule="after completion")
        first = live.id
        _, live = await resolve_check(session, live.id, CheckOutcome.PASSED)
        other = await create_check(session, title="Slept 7 hours?")
        run = await create_card(session, kind="action", title="Run", stage="today")
        swim = await create_card(session, kind="action", title="Swim", stage="today")
        # A later copy names the same series, by the id it keeps; its title may follow the id.
        for items in (f"Check #{live.id}", f" check#{first} ", f"Check #{first} «Sat straight?»"):
            assert await items_series(session, items) == ("check", (first,))
        assert await items_series(session, f"Check #{live.id} и Check #{other.id}") == (
            "check",
            (first, other.id),
        )
        assert await items_series(session, f"Action #{swim.id} and Action #{run.id}") == (
            "action",
            (run.id, swim.id),
        )


async def test_the_items_are_checked_before_the_owner_sees_them(sessions):
    """RM-COND-031 — tests/brd/reminders.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?")
        run = await create_card(session, kind="action", title="Run", stage="today")
        for items, error in (
            ("any Check", NAME_BY_ID),
            (f"Checks of Action #{run.id}", NAME_BY_ID),
            (f"#{check.id}", NAME_BY_ID),
            (f"Check #{check.id}, Action #{run.id}", ONE_KIND),
            ("Check #999", "Check #999 does not exist."),
            (f"Action #{run.id}, Action #998", "Action #998 does not exist."),
        ):
            with pytest.raises(ConditionError, match=re.escape(error)):
                await items_series(session, items)


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
    assert "The user asked to be reminded of: Cheer the owner on." in first
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
        await condition_reminder(session, of_check(live.id, "any", 2, in_row=False))
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
        await condition_reminder(session, of_check(live.id, "any", 2, in_row=False))
        await condition_reminder(session, of_check(live.id, "yes", 2))
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


@pytest.mark.parametrize(("answer", "in_row"), [("any", False), ("yes", True)])
async def test_answers_saved_together_count_one_by_one(sessions, answer, in_row):
    """RM-COND-032 — tests/brd/reminders.feature"""
    sink = hook_sink(sessions)
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Go to the market", stage="today")
        check_ids = []
        for title in ("Milk", "Bread", "Eggs", "Tea"):
            check = await create_check(session, title=title)
            await toggle_card_check(session, card.id, check.id)
            check_ids.append(check.id)
        await condition_reminder(session, of_items("check", check_ids, answer, 3, in_row=in_row))
        await session.commit()
        card_id = card.id

    async with sessions() as session:
        outcomes = {check_id: CheckOutcome.PASSED for check_id in check_ids}
        await finish_action(session, card_id, check_outcomes=outcomes)
        await session.commit()
    await sink.drain()

    # One Done answers every Check at once; each answer counts at its own place, so the third
    # says it once, and the fourth says nothing.
    (cue,) = await said(sessions)
    row = " in a row" if in_row else ""
    assert cue.endswith(f"3{row} now; 3 Passed and 0 Missed in all")


async def test_done_of_actions_counts_in_all(sessions):
    """RM-COND-032 — tests/brd/reminders.feature"""
    sink = hook_sink(sessions)
    async with sessions() as session:
        runs = [
            await create_card(session, kind="action", title=title, stage="today")
            for title in ("Run", "Swim", "Lift")
        ]
        reading = await create_card(session, kind="action", title="Read", stage="today")
        sport = of_items("action", [run.id for run in runs], "yes", 2)
        await condition_reminder(session, sport)
        await session.commit()

    async def done(card_id: int) -> list[str]:
        async with sessions() as session:
            await finish_action(session, card_id)
            await session.commit()
        await sink.drain()
        return await said(sessions)

    assert await done(runs[0].id) == []
    assert await done(reading.id) == []  # not one of its series
    (cue,) = await done(runs[1].id)
    assert f"every 2 Done of {sport.items}; 2 Done now" in cue
    assert await done(runs[2].id) == [cue]


async def test_the_series_are_fixed_when_the_reminder_is_set(sessions):
    """RM-COND-033 — tests/brd/reminders.feature"""
    sink = hook_sink(sessions)
    async with sessions() as session:
        tag = await create_tag(session, "sport")
        run = await create_card(session, kind="action", title="Run", stage="today", tag_ids={tag.id})
        each = of_items("action", [run.id], "yes", 1)
        reminder = await condition_reminder(session, each)
        await session.commit()
        later = await create_card(
            session, kind="action", title="Swim", stage="today", tag_ids={tag.id}
        )
        await session.commit()

    async def done(card_id: int) -> int:
        async with sessions() as session:
            await finish_action(session, card_id)
            await session.commit()
        await sink.drain()
        return len(await said(sessions))

    # An Action made later does not count, even with the same Tag.
    assert await done(later.id) == 0
    assert await done(run.id) == 1

    async with sessions() as session:
        await delete_card(session, run.id)
        await session.commit()
        assert await session.get(Reminder, reminder.id) is not None
        assert await found_ids(session, each) == []
        assert await finds_text(session, each, []) == "Nothing: its items were deleted."


async def test_a_series_counts_after_its_first_copy_is_deleted(sessions):
    """RM-COND-033 — tests/brd/reminders.feature"""
    sink = hook_sink(sessions)
    async with sessions() as session:
        live = await create_check(session, title="Sat straight?", schedule="after completion")
        first = live.id
        _, live = await resolve_check(session, live.id, CheckOutcome.PASSED)
        kind, series = await items_series(session, f"Check #{live.id}")
        each = Condition(items=f"Check #{first}", series=series, kind=kind, answer="yes", count=1)
        await condition_reminder(session, each)
        await delete_check(session, first)
        await session.commit()
        live_id = live.id
        assert await found_ids(session, each) == [live_id]

    async with sessions() as session:
        await resolve_check(session, live_id, CheckOutcome.PASSED)
        await session.commit()
    await sink.drain()
    (cue,) = await said(sessions)
    assert cue.endswith("1 now; 1 Passed and 0 Missed in all")


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

        async def never(condition: Condition) -> str | None:
            return await never_reached(session, condition, await found_ids(session, condition))

        assert await never(of_check(alone.id)) == (
            f"Check #{alone.id} does not repeat, so a count of 3 never comes."
        )
        assert await never(of_check(alone.id, count=1)) is None
        assert await never(of_check(endless.id, count=99)) is None
        assert await never(of_check(limited.id, count=5)) is None
        assert await never(of_check(limited.id, count=6)) == (
            f"Check #{limited.id} counts 0 now and has 5 answers left, so a count of 6 never comes."
        )
        assert await never(of_check(on_daily.id, count=99)) is None
        assert await never(of_check(on_once.id)) == (
            f"Check #{on_once.id} does not repeat, so a count of 3 never comes."
        )
        # More than one series takes any count.
        assert await never(of_items("check", [alone.id, on_once.id], "no", 9)) is None
        market = of_items("action", [once.id], "yes", 2)
        assert await never(market) == (
            f"Action #{once.id} does not repeat, so a count of 2 never comes."
        )

        # The answers it has had count with what is left of a new revision's total.
        live = await create_check(session, title="Walked?", schedule="after completion")
        first = live.id
        for _ in range(2):
            _, live = await resolve_check(session, live.id, CheckOutcome.PASSED)
        await update_check_fields(
            session,
            live.id,
            {
                "schedule": "after completion, 2 times",
                "schedule_rule": {"kind": "after_completion", "limit": 2},
            },
        )
        await session.commit()
        assert await never(of_check(first, "yes", 4, in_row=False)) is None
        assert await never(of_check(first, "yes", 5, in_row=False)) == (
            f"Check #{first} counts 2 now and has 2 answers left, so a count of 5 never comes."
        )


async def test_a_condition_is_listed_and_opened_like_any_other(sessions):
    """RM-COND-035 — tests/brd/reminders.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        reminder = await condition_reminder(session, of_check(check.id))
        await create_reminder(
            session,
            remind_text="Take a walk.",
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

    # With its items deleted, it says so on the screen.
    async with sessions() as session:
        await delete_check(session, check_id)
        await session.commit()
    await render_reminder(detail, services, reminder_id)
    assert "🔎 Finds now: Nothing: its items were deleted." in detail.edits[-1][0]


async def test_safwa_reads_a_condition_in_ai_reminders(read_views):
    """RM-COND-035 — tests/brd/reminders.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        await condition_reminder(session, of_check(check.id))
        await session.commit()

    (row,) = (
        await runner.run(
            "SELECT schedule_kind, json_extract(condition, '$.kind') AS kind, "
            "json_extract(condition, '$.answer') AS answer, "
            "json_extract(condition, '$.count') AS count, "
            "json_extract(condition, '$.in_row') AS in_row, "
            "json_extract(condition, '$.once') AS once, "
            "json_extract(condition, '$.series') AS series, next_fire_at_local FROM ai_reminders"
        )
    ).rows
    assert row == {
        "schedule_kind": "condition",
        "kind": "check",
        "answer": "no",
        "count": 3,
        "in_row": 1,
        "once": 0,
        "series": f"[{check.id}]",
        "next_fire_at_local": None,
    }


def test_an_unclear_time_gets_the_time_forms():
    """RM-UNCLEAR-028 — tests/brd/reminders.feature"""
    assert REMINDER_TIME_FORMS == (
        "tomorrow at 15:00",
        "on Thursday at 10:00",
        "every weekday at 8:00",
        "every 2 hours",
    )
    told = str(unclear("No hour was given."))
    assert told.splitlines()[0] == "No hour was given."
    assert all(form in told for form in REMINDER_TIME_FORMS)
