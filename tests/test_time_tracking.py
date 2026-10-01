"""The time an Action took: the Card carries it, the Profile switches its tracking, the
Advisor asks after it, and a Sprint's retro keeps it.

Every test here is evidence for one scenario in `tests/brd/cards.feature`,
`tests/brd/profile.feature` or `tests/brd/retro.feature`.
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.bootstrap.modules import PROPOSALS, REGISTRY
from safwa.features.cards.agent import CARD_AUTOAPPROVALS, CardToolInput
from safwa.features.cards.hooks import (
    TIME_TRACKING_REMINDER_HOOK,
    time_tracking_request,
)
from safwa.features.cards.model import (
    TRACKED_MINS_MAX,
    Card,
    CardStage,
    Category,
    minutes_label,
)
from safwa.features.cards.telegram import render_card
from safwa.features.cards.telegram.review import CardProposalPresenter
from safwa.features.cards.telegram.text_input import TIME_SPENT_INSTRUCTION
from safwa.features.cards.use_cases import (
    CARD_DONE,
    archive_subtree,
    create_card,
    delete_one_card,
    finish_action,
    move_card,
    toggle_card_category,
    update_card_fields,
)
from safwa.features.planning.closing import LONGEST_SHOWN, RetroStatistics, TimedAction
from safwa.features.planning.model import Sprint
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.profile.api import (
    TIME_TRACKING_REMINDER,
    active_day_minutes,
    hook_switched_on,
    set_hook_switch,
    time_tracking_on,
)
from safwa.features.profile.model import (
    DIARY_TIME_DEFAULT,
    MORNING_TIME_DEFAULT,
    ProfileField,
    UserProfile,
)
from safwa.features.profile.telegram import command_profile
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.retro.analysis import OVERVIEW_PROMPT, overview_text
from safwa.features.retro.telegram import open_retro
from safwa.features.retro.use_cases import analysis_input
from tg_agent_shell.cues.queue import add_hook_cue
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.hooks.contracts import OnCommitted
from tg_agent_shell.proposals.api import ApplyContext, ChangeAction, ToolPreparationError
from tg_agent_shell.proposals.model import ProposalChange
from tg_agent_shell.proposals.prepare import ChangePreparer
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text


async def _track_time(sessions, on: bool = True) -> None:
    async with sessions() as session:
        await set_profile_field(session, ProfileField.TIME_TRACKING, on)
        await session.commit()


async def _prepared(session, arguments: dict) -> ProposalChange:
    """A `card` call checked the way a proposal is, ready for Save."""
    change = PROPOSALS.change_from_tool("card", arguments)
    prepared = await ChangePreparer(None, None, PROPOSALS).prepare(  # type: ignore[arg-type]
        session, change
    )
    return ProposalChange(
        entity="card",
        action=ChangeAction(change.action),
        entity_id=change.id,
        expected_version=prepared.expected_version,
        values=prepared.values,
    )


async def _save(session, arguments: dict) -> None:
    change = await _prepared(session, arguments)
    await PROPOSALS.handler("card").apply(ApplyContext(session, frozenset()), change)


async def _press(message, services, markup, text: str) -> None:
    button = next(item for row in markup.inline_keyboard for item in row if item.text == text)
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )


# --------------------------------------------------------------------------- the Card


async def test_cd_time_039_an_action_carries_the_minutes_it_took(sessions):
    """CD-TIME-039 — tests/brd/cards.feature"""
    assert (minutes_label(331), minutes_label(120), minutes_label(45)) == ("5h 31m", "2h", "45m")
    assert "tracked_mins" not in inspect.signature(create_card).parameters
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Reports")
        report = await create_card(
            session, kind="action", title="Write the report", effort_points=3, parent_id=goal.id
        )
        await session.commit()
        await update_card_fields(session, report.id, {"tracked_mins": 331})
        assert report.tracked_mins == 331
        for accepted in (1, TRACKED_MINS_MAX):
            await update_card_fields(session, report.id, {"tracked_mins": accepted})
            assert report.tracked_mins == accepted
        await session.commit()
        for refused in (0, TRACKED_MINS_MAX + 1, 1.5, True, "90"):
            with pytest.raises(DomainError, match="minutes"):
                await update_card_fields(session, report.id, {"tracked_mins": refused})
            await session.rollback()
            await session.refresh(report)
            assert report.tracked_mins == TRACKED_MINS_MAX
        await update_card_fields(session, report.id, {"tracked_mins": None})
        assert report.tracked_mins is None
        with pytest.raises(DomainError, match="Action-only"):
            await update_card_fields(session, goal.id, {"tracked_mins": 30})

        # The next instance of a series starts with no time of its own.
        run = await create_card(
            session, kind="action", title="Run", effort_points=2, repeatable=True
        )
        await update_card_fields(session, run.id, {"tracked_mins": 40})
        [successor_id] = (await finish_action(session, run.id)).successor_ids
        assert (run.tracked_mins, (await session.get(Card, successor_id)).tracked_mins) == (40, None)

        # Reopening keeps what was spent; an archived Action's time is not changed.
        await finish_action(session, report.id, tracked_mins=90)
        await move_card(session, report.id, CardStage.BACKLOG)
        assert report.tracked_mins == 90
        await finish_action(session, report.id)
        await archive_subtree(session, report.id)
        with pytest.raises(DomainError, match="archived"):
            await update_card_fields(session, report.id, {"tracked_mins": 120})


async def test_cd_time_039_safwa_proposes_the_time_on_an_action_or_with_finishing_it(sessions):
    """CD-TIME-039 — tests/brd/cards.feature"""
    with pytest.raises(ValidationError, match="no time spent yet"):
        CardToolInput(mode="create", kind="action", title="New", effort_points=1, tracked_mins=30)
    for out_of_range in (0, TRACKED_MINS_MAX + 1):
        with pytest.raises(ValidationError):
            CardToolInput(mode="update", id=1, tracked_mins=out_of_range)
    with pytest.raises(ValidationError, match="accepts only tracked_mins"):
        CardToolInput(mode="complete", id=1, tracked_mins=30, title="Renamed")
    assert CardToolInput(mode="update", id=1, tracked_mins=None).model_dump(
        exclude_unset=True
    ) == {"mode": "update", "id": 1, "tracked_mins": None}
    # A time the owner named is a plain correction; finishing never saves itself.
    assert "tracked_mins" in CARD_AUTOAPPROVALS["update"].allowed_fields
    assert "complete" not in CARD_AUTOAPPROVALS

    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Reports")
        report = await create_card(
            session, kind="action", title="Write the report", effort_points=3, stage="today"
        )
        await session.commit()
        # Off in the Profile, the words are still recorded.
        assert await time_tracking_on(session) is False

        completing = await _prepared(
            session, {"mode": "complete", "id": report.id, "tracked_mins": 120}
        )
        presenter = CardProposalPresenter()
        details = await presenter.details(session, completing, None)
        assert details == ["Stage: today → done", "Time spent: — → 2h"]
        assert (await presenter.summary(session, completing, details)).endswith("(2h spent)")
        await PROPOSALS.handler("card").apply(ApplyContext(session, frozenset()), completing)
        await session.refresh(report)
        assert (report.effective_stage, report.tracked_mins) == (CardStage.DONE.value, 120)

        await _save(session, {"mode": "update", "id": report.id, "tracked_mins": "null"})
        await session.refresh(report)
        assert report.tracked_mins is None
        with pytest.raises(DomainError, match="no applicable fields"):
            await _prepared(session, {"mode": "update", "id": goal.id, "tracked_mins": 30})

        # A finished repeat takes its own time, and that is the one change it takes.
        run = await create_card(session, kind="action", title="Run", effort_points=2, repeatable=True)
        [live_id] = (await finish_action(session, run.id)).successor_ids
        await session.commit()
        await _save(session, {"mode": "update", "id": run.id, "tracked_mins": 45})
        await session.refresh(run)
        assert (run.tracked_mins, (await session.get(Card, live_id)).tracked_mins) == (45, None)
        with pytest.raises(ToolPreparationError) as refused:
            await _prepared(session, {"mode": "update", "id": run.id, "title": "Run far"})
        assert refused.value.code == "closed_repeat"


async def test_cd_time_039_the_card_screen_shows_and_takes_the_time(sessions):
    """CD-TIME-039 — tests/brd/cards.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Write the report", effort_points=3)
        await session.commit()
        card_id = card.id
    services = services_for(sessions)

    # Off in the Profile: no control for the time.
    message = FakeMessage(60, bot_message=True)
    await render_card(message, services, card_id, full=True)
    assert "⌛ Time spent" not in button_texts(message.edits[-1][1])

    await _track_time(sessions)
    message = FakeMessage(61, bot_message=True)
    await render_card(message, services, card_id, full=True)
    markup = message.edits[-1][1]
    for number, typed in enumerate(("331", "5:31", "5h 31m")):
        await _press(message, services, markup, "⌛ Time spent")
        answer = FakeMessage(62 + number, text=typed, bot_message=False, bot=message.bot)
        await ordinary_text(answer, services)
        async with sessions() as session:
            assert (await session.get(Card, card_id)).tracked_mins == 331
        assert "Time spent: 5h 31m" in message.bot.edits[-1][1]
        markup = message.bot.edits[-1][2]

    await _press(message, services, markup, "⌛ Time spent")
    for number, typed in enumerate(("tomorrow", str(TRACKED_MINS_MAX + 1), "0")):
        await ordinary_text(FakeMessage(70 + number, text=typed, bot_message=False, bot=message.bot), services)
        assert TIME_SPENT_INSTRUCTION in message.bot.edits[-1][1]
    async with sessions() as session:
        assert (await session.get(Card, card_id)).tracked_mins == 331
    await ordinary_text(FakeMessage(80, text="off", bot_message=False, bot=message.bot), services)
    async with sessions() as session:
        assert (await session.get(Card, card_id)).tracked_mins is None

    # Compact and full alike, and whether the Profile tracks time or not.
    async with sessions() as session:
        await update_card_fields(session, card_id, {"tracked_mins": 45})
        await session.commit()
    await _track_time(sessions, on=False)
    for full in (False, True):
        shown = FakeMessage(90 + full, bot_message=True)
        await render_card(shown, services, card_id, full=full)
        assert "Time spent: 45m" in shown.edits[-1][0]


async def test_cd_time_040_a_goal_shows_the_time_of_the_actions_under_it(sessions):
    """CD-TIME-040 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Move house")
        empty = await create_card(session, kind="goal", title="Learn Spanish")
        subgoal = await create_card(session, kind="subgoal", title="Pack", parent_id=goal.id)
        books, kitchen, _ = [
            await create_card(
                session, kind="action", title=title, effort_points=1, parent_id=subgoal.id
            )
            for title in ("Books", "Kitchen", "Garage")
        ]
        await create_card(session, kind="action", title="Call", effort_points=1, parent_id=empty.id)
        await update_card_fields(session, books.id, {"tracked_mins": 30})
        await finish_action(session, kitchen.id, tracked_mins=45)
        await archive_subtree(session, kitchen.id)
        await session.commit()
        assert (goal.tracked_mins, subgoal.tracked_mins, empty.tracked_mins) == (75, 75, None)
        goal_id = goal.id

    shown = FakeMessage(95, bot_message=True)
    await render_card(shown, services_for(sessions), goal_id)
    assert "Time spent: 1h 15m" in shown.edits[-1][0]


# ------------------------------------------------------------------------ the question


def _reminder_evaluations(evaluations) -> list:
    return [item for item in evaluations if item.spec.name == TIME_TRACKING_REMINDER]


async def test_cd_time_041_the_request_names_what_is_still_done_and_without_a_time(sessions):
    """CD-TIME-041 — tests/brd/cards.feature"""
    assert TIME_TRACKING_REMINDER_HOOK.agent_related
    assert TIME_TRACKING_REMINDER_HOOK.name == TIME_TRACKING_REMINDER
    assert TIME_TRACKING_REMINDER_HOOK.on == (OnCommitted(kind=CARD_DONE),)
    assert TIME_TRACKING_REMINDER_HOOK in REGISTRY.hooks.specs
    async with sessions() as session:
        cards = [
            await create_card(session, kind="action", title=title, effort_points=points)
            for title, points in (
                ("Write the report", 3), ("Call the bank", 1), ("Fix the bike", 2),
                ("Read the contract", 1), ("Renew the passport", 1),
            )
        ]
        ids = [card.id for card in cards]
        for card_id in ids:
            await finish_action(session, card_id)
        await move_card(session, ids[2], CardStage.TODAY)
        await archive_subtree(session, ids[3])
        await delete_one_card(session, ids[4])
        await update_card_fields(session, ids[1], {"tracked_mins": 15})
        # Finished with its time in the same step: nothing to ask.
        timed = await create_card(session, kind="action", title="Sign", effort_points=1)
        await finish_action(session, timed.id, tracked_mins=10)
        await session.commit()

        request = await time_tracking_request(session, [*ids, timed.id])
        assert request is not None
        assert f"- #{ids[0]} «Write the report»" in request
        assert "EP" not in request
        assert "not on its open repeat" in request
        for absent in ("Call the bank", "Fix the bike", "Read the contract", "Renew", "Sign"):
            assert absent not in request
        assert await time_tracking_request(session, ids[1:] + [timed.id]) is None

        # A repeat is asked about by the instance that was finished.
        run = await create_card(session, kind="action", title="Run", effort_points=2, repeatable=True)
        [live_id] = (await finish_action(session, run.id)).successor_ids
        request = await time_tracking_request(session, [run.id])
        assert f"#{run.id} «Run»" in request and f"#{live_id}" not in request


async def test_cd_time_041_nothing_is_asked_while_time_tracking_or_the_reminder_is_off(sessions):
    """CD-TIME-041 — tests/brd/cards.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Write the report", effort_points=3)
        await finish_action(session, card.id)
        await add_hook_cue(session, hook=TIME_TRACKING_REMINDER, items=[card.id])
        await session.commit()
        card_id = card.id
    done = Committed(CARD_DONE, card_id)

    async def asked() -> tuple[int, str | None]:
        evaluations = [item async for item in REGISTRY.hooks.evaluate(done, sessions)]
        words = await REGISTRY.hooks.prepare(sessions, TIME_TRACKING_REMINDER, [card_id])
        return len(_reminder_evaluations(evaluations)), words

    # A new workspace: the reminder's own switch is on, and still nothing is asked.
    async with sessions() as session:
        assert TIME_TRACKING_REMINDER not in (await session.get(UserProfile, 1)).disabled_hooks
    assert await asked() == (0, None)

    await _track_time(sessions)
    checked, words = await asked()
    assert checked == 1 and "«Write the report»" in words

    async with sessions() as session:
        await set_hook_switch(session, TIME_TRACKING_REMINDER, on=False)
        await session.commit()
        assert await hook_switched_on(session, TIME_TRACKING_REMINDER) is False
    assert await asked() == (0, None)

    async with sessions() as session:
        await set_hook_switch(session, TIME_TRACKING_REMINDER, on=True)
        await add_hook_cue(session, hook=TIME_TRACKING_REMINDER, items=[card_id])
        await session.commit()
    # A question written while tracking was on is not said once it is off.
    await _track_time(sessions, on=False)
    assert await asked() == (0, None)


# ------------------------------------------------------------------------- the Profile


async def test_ps_time_017_time_tracking_is_off_until_the_owner_switches_it_on(sessions):
    """PS-TIME-017 — tests/brd/profile.feature"""
    services = services_for(sessions)
    services.hooks = REGISTRY.hooks
    message = FakeMessage(960, bot_message=True, answer_as_new=True)
    await command_profile(message, services)
    rendered, markup = message.edits[-1]
    assert (
        f"Time tracking: off — records the time an Action took; your active day runs from the "
        f"Morning time to the Diary time, {MORNING_TIME_DEFAULT} to {DIARY_TIME_DEFAULT}."
    ) in rendered
    assert "⌛ Time tracking: off" in button_texts(markup)
    assert "Time tracking reminder" not in rendered
    assert not any("Time tracking reminder" in label for label in button_texts(markup))
    await _press(message, services, markup, "🔔 Hooks")
    rendered, markup = message.bot.edits[-1][1:]
    assert not any("Time tracking reminder" in label for label in button_texts(markup))
    await _press(message, services, markup, "↩️ Back")
    rendered, markup = message.bot.edits[-1][1:]

    await _press(message, services, markup, "⌛ Time tracking: off")
    rendered, markup = message.bot.edits[-1][1:]
    assert "Time tracking switched on." in rendered
    assert "⌛ Time tracking: on" in button_texts(markup)
    assert "Time tracking reminder" not in rendered
    await _press(message, services, markup, "🔔 Hooks")
    rendered, markup = message.bot.edits[-1][1:]
    assert "🔔 Time tracking reminder: on" in button_texts(markup)
    await _press(message, services, markup, "🔔 Time tracking reminder: on")
    rendered, markup = message.bot.edits[-1][1:]
    assert TIME_TRACKING_REMINDER_HOOK.description in rendered
    await _press(message, services, markup, "↩️ Back")
    await _press(message, services, message.bot.edits[-1][2], "↩️ Back")
    rendered, markup = message.bot.edits[-1][1:]
    async with sessions() as session:
        assert await time_tracking_on(session) is True

    await _press(message, services, markup, "⌛ Time tracking: on")
    rendered, markup = message.bot.edits[-1][1:]
    assert "Time tracking switched off." in rendered
    assert not any("Time tracking reminder" in label for label in button_texts(markup))
    await _press(message, services, markup, "🔔 Hooks")
    assert not any(
        "Time tracking reminder" in label for label in button_texts(message.bot.edits[-1][2])
    )
    async with sessions() as session:
        assert await time_tracking_on(session) is False
        with pytest.raises(DomainError, match="on or off"):
            await set_profile_field(session, ProfileField.TIME_TRACKING, "yes")


async def test_ps_time_017_the_active_day_runs_from_the_morning_time_to_the_diary_time(sessions):
    """PS-TIME-017 — tests/brd/profile.feature"""
    from datetime import time

    async with sessions() as session:
        assert await active_day_minutes(session) == 13 * 60
        await set_profile_field(session, ProfileField.DIARY_TIME, time(1, 0))
        assert await active_day_minutes(session) == 16 * 60
        await set_profile_field(session, ProfileField.MORNING_TIME, time(1, 0))
        assert await active_day_minutes(session) == 0


# --------------------------------------------------------------------------- the retro


async def _sprint(sessions, spent: dict[str, tuple[int | None, float, tuple[str, ...]]]) -> int:
    """One Sprint, run and ended on one day, whose Actions each finished with that time."""
    async with sessions() as session:
        cards = {
            title: await create_card(
                session, kind="action", title=title, stage="sprint", effort_points=points
            )
            for title, (_, points, _) in spent.items()
        }
        for title, (_, _, categories) in spent.items():
            for category in categories:
                await toggle_card_category(session, cards[title].id, Category(category))
        sprint = await start_sprint(session, success_criteria="Ship v2")
        for title, (minutes, _, _) in spent.items():
            await finish_action(session, cards[title].id, tracked_mins=minutes)
        await finish_sprint(session)
        await session.commit()
        return sprint.id


SPENT = {
    "Quarterly report": (120, 3, ("work",)),
    "Move": (60, 5, ("work", "self")),
    "Tax return": (60, 2, ()),
    "Walk": (30, 1, ("rest",)),
    "Call": (None, 1, ("work",)),
}


async def test_rt_time_009_a_sprint_that_ends_with_time_tracking_on_keeps_its_time(sessions):
    """RT-TIME-009 — tests/brd/retro.feature"""
    await _track_time(sessions)
    sprint_id = await _sprint(sessions, SPENT)
    async with sessions() as session:
        statistics = RetroStatistics.from_record((await session.get(Sprint, sprint_id)).retro)
        # Written down as the Sprint ended: a time recorded afterwards changes nothing.
        call = await session.scalar(select(Card).where(Card.title == "Call"))
        await update_card_fields(session, call.id, {"tracked_mins": 500})
        await session.commit()
        again = RetroStatistics.from_record((await session.get(Sprint, sprint_id)).retro)
    assert again == statistics
    assert (statistics.time_tracking, statistics.active_day_minutes) == (True, 13 * 60)
    assert (statistics.minutes, statistics.timed, statistics.timed_effort) == (270, 4, 11)
    assert LONGEST_SHOWN == 3
    assert statistics.longest == (
        TimedAction("Quarterly report", 120), TimedAction("Move", 60), TimedAction("Tax return", 60)
    )
    work, self_, none = (statistics.by_category[name] for name in ("work", "self", "none"))
    assert (work.minutes, work.timed_count, work.timed_effort) == (180, 2, 8)
    assert (self_.minutes, self_.timed_count, none.minutes, none.timed_count) == (60, 1, 60, 1)
    assert statistics.by_energy["none"].minutes == 270
    assert statistics.day_share == round(100 * 270 / (13 * 60))

    # Off as it ends, or closed before the record kept time: no time at all.
    await _track_time(sessions, on=False)
    untracked = await _sprint(sessions, {"Walk again": (30, 1, ())})
    async with sessions() as session:
        record = (await session.get(Sprint, untracked)).retro
    statistics = RetroStatistics.from_record(record)
    assert (statistics.time_tracking, statistics.minutes, statistics.longest) == (False, 0, ())
    assert statistics.day_share is None
    older = {key: value for key, value in record.items() if key not in {
        "time_tracking", "active_day_minutes", "minutes", "timed", "timed_effort", "longest",
    }}
    assert RetroStatistics.from_record(older) == statistics


async def test_rt_time_010_the_retro_shows_how_the_sprints_time_went(sessions, effort_on):
    """RT-TIME-010 — tests/brd/retro.feature"""
    await _track_time(sessions)
    sprint_id = await _sprint(sessions, {**SPENT, "Draft <b>": (15, 1, ("work",))})
    # Shown whatever the Profile says now.
    await _track_time(sessions, on=False)
    message = FakeMessage(330, bot_message=True)
    await open_retro(message, services_for(sessions), sprint_id)
    text = message.edits[-1][0]
    for line in (
        "<b>Time</b>",
        "Tracked 4h 45m, 4h 45m a day — 37% of a 13h active day",
        "2.5 EP an hour; recorded on 5 of 6 finished Actions (83%)",
        "By Category: time · share · per Action · EP an hour",
        "work 3h 15m · 57% · 1h 5m · 2.8",
        "self 1h · 17% · 1h · 5.0",
        "none 1h · 17% · 1h · 2.0",
        "rest 30m · 9% · 30m · 2.0",
        "By Energy type: time · share · per Action · EP an hour",
        "none 4h 45m · 100% · 57m · 2.5",
        "Longest: «Quarterly report» 2h, «Move» 1h, «Tax return» 1h",
    ):
        assert line in text, line
    assert text.index("<b>Actions</b>") < text.index("<b>Time</b>")
    # A bucket with no time on it is not shown.
    assert "contribution" not in text and "cognitive" not in text

    untracked = await _sprint(sessions, {"Walk again": (30, 1, ())})
    message = FakeMessage(331, bot_message=True)
    await open_retro(message, services_for(sessions), untracked)
    assert "<b>Time</b>" not in message.edits[-1][0]


async def test_rt_time_011_the_analysis_reads_the_share_of_the_active_day_and_nothing_else(
    sessions,
):
    """RT-TIME-011 — tests/brd/retro.feature"""
    assert (
        "The share of the active day tracked is how well the user kept their time; "
        "set it beside how the Sprint went."
    ) in OVERVIEW_PROMPT
    first = await _sprint(sessions, {"Walk": (30, 1, ())})
    async with sessions() as session:
        untracked = (await analysis_input(session, first)).sprints
    assert "Time" not in overview_text(untracked)

    await _track_time(sessions)
    second = await _sprint(sessions, {"Quarterly report": (156, 3, ("work",))})
    async with sessions() as session:
        compared = (await analysis_input(session, second)).sprints
    text = overview_text(compared)
    assert "- Time: not tracked" in text
    assert "- Time tracked: 20% of the active day on average" in text
    assert text.index("- Time: not tracked") < text.index("- Time tracked: 20%")
    # No other number of time: not the minutes, the coverage, the buckets or the longest.
    for absent in ("156", "2h 36m", "Quarterly report", "EP an hour"):
        assert absent not in text
    # A share that cannot be worked out is not read as untracked.
    [*before, this] = compared
    zero_day = replace(this, statistics=replace(this.statistics, active_day_minutes=0))
    assert overview_text([*before, zero_day]).count("- Time") == 1
