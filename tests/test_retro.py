"""The retro screen: what a Sprint leaves behind once it has closed."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

import safwa.features.planning.use_cases as planning_use_cases
from llm_gateway import ToolCall
from safwa.features.cards.model import CardStage
from safwa.features.cards.use_cases import (
    create_card,
    delete_subtree,
    finish_action,
    move_card,
    toggle_card_check,
    update_card_fields,
)
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import create_check, resolve_check, toggle_check_value
from safwa.features.planning.closing import RetroStatistics, SeriesTally
from safwa.features.planning.model import Sprint
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.retro.agent import RETRO_AGENT
from safwa.features.retro.records import SUMMED, aggregate
from safwa.features.retro.telegram import RETRO_LIST_PAGE_SIZE, open_retro, render_retro_list
from safwa.features.values.use_cases import create_value
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.manifest import AgentContext


async def test_rt_open_001_a_sprint_that_ended_keeps_a_screen_of_its_own(sessions, effort_on) -> None:
    """RT-OPEN-001 — tests/brd/retro.feature"""
    async with sessions() as session:
        await create_card(
            session, kind="action", title="Planned", stage="sprint", effort_points=2
        )
        sprint = await start_sprint(session, success_criteria="Ship v2")
        await session.commit()

    message = FakeMessage(320, bot_message=True)

    # While it runs there is nothing to look back on.
    with pytest.raises(DomainError, match="has not ended"):
        await open_retro(message, services_for(sessions), sprint.id)
    async with sessions() as session:
        await finish_sprint(session)
        await session.commit()

    await open_retro(message, services_for(sessions), sprint.id)

    text, markup = message.edits[-1]
    assert f"Sprint {sprint.number} retro" in text
    assert str(sprint.planned_start_date) in text
    assert str(sprint.planned_end_date) in text
    assert "Ship v2" in text
    assert "Taken 2 EP, finished 0 EP (0%)" in text
    assert "Met: not marked yet" in text
    assert button_texts(markup) == ["✅ Met", "❌ Not met", "🔎 Analyse with AI", "↩️ Menu"]


async def test_rt_stats_003_the_retro_adds_the_sprint_up_from_its_record(sessions, effort_on) -> None:
    """RT-STATS-003 — tests/brd/retro.feature"""
    async with sessions() as session:
        health = await create_value(session, "Health")
        shipped, stuck, dropped = [
            await create_card(session, kind="action", title=title, stage="sprint", effort_points=points)
            for title, points in (("Ship it", 5), ("Stuck", 3), ("Dropped", 2))
        ]
        sprint = await start_sprint(session, success_criteria="Ship v2")
        joined = await create_card(
            session, kind="action", title="Joined", stage="sprint", effort_points=3
        )
        await move_card(session, dropped.id, CardStage.BACKLOG)
        await finish_action(session, shipped.id)
        await update_card_fields(
            session, stuck.id, {"blocked": True, "blocked_description": "Waiting"}
        )
        # A repeating Check on a Value, answered three times while the Sprint ran; one on
        # no Value, answered too.
        habit = await create_check(session, title="Ran before work?", repeatable=True)
        await toggle_card_check(session, joined.id, habit.id)
        await toggle_check_value(session, habit.id, health.id)
        live = habit
        for outcome in (CheckOutcome.PASSED, CheckOutcome.MISSED, CheckOutcome.PASSED):
            _, live = await resolve_check(session, live.id, outcome)
        loose = await create_check(session, title="Slept well?")
        await resolve_check(session, loose.id, CheckOutcome.MISSED)
        await finish_sprint(session)
        await session.commit()

        statistics = RetroStatistics.from_record((await session.get(Sprint, sprint.id)).retro)
    assert (statistics.initial, statistics.added, statistics.removed) == (10, 3, 2)
    assert (statistics.taken, statistics.done, statistics.done_share) == (13, 5, 38)
    assert (statistics.finished, statistics.remaining, statistics.blocked) == (1, 2, 1)
    assert statistics.series == (SeriesTally("Ran before work?", ("Health",), 2, 1),)

    message = FakeMessage(321, bot_message=True)
    await open_retro(message, services_for(sessions), sprint.id)
    text, _ = message.edits[-1]
    for line in (
        "Taken 13 EP, finished 5 EP (38%)",
        "Initial plan 10 EP, added 3 EP, taken out 2 EP",
        "Finished 1, remaining 2, of them blocked 1",
        "Ran before work? (Health): Passed 2, Missed 1",
    ):
        assert line in text
    assert "Slept well?" not in text

    # Written down as the Sprint ended: what happens to its Actions and Checks afterwards
    # changes nothing on the screen.
    async with sessions() as session:
        await update_card_fields(session, stuck.id, {"blocked": False})
        await delete_subtree(session, joined.id)
        await toggle_check_value(session, live.id, health.id)
        await session.commit()
    again = FakeMessage(322, bot_message=True)
    await open_retro(again, services_for(sessions), sprint.id)
    visible = again.edits[-1][0]
    for line in (
        "Taken 13 EP, finished 5 EP (38%)",
        "Finished 1, remaining 2, of them blocked 1",
        "Ran before work? (Health): Passed 2, Missed 1",
    ):
        assert line in visible


async def _ended(sessions, count: int) -> list[Sprint]:
    """`count` Sprints started and finished one after another, the oldest first."""
    ended = []
    async with sessions() as session:
        await create_card(session, kind="action", title="Planned", stage="sprint", effort_points=2)
        for index in range(count):
            # The Action keeps its stage when a Sprint ends, so each next one has work.
            sprint = await start_sprint(session, success_criteria=f"Goal {index + 1}")
            await finish_sprint(session)
            ended.append(sprint)
        await session.commit()
    return ended


async def _tap(message: FakeMessage, services, text: str) -> None:
    """Press the button with this text on the screen the message holds now."""
    _, markup = message.edits[-1]
    [button] = [
        button for row in markup.inline_keyboard for button in row if button.text == text
    ]
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )


async def test_rt_list_012_every_sprint_that_ended_is_one_tap_from_the_menu(sessions) -> None:
    """RT-LIST-012 — tests/brd/retro.feature"""
    services = services_for(sessions)
    empty = FakeMessage(500, bot_message=True)
    await render_retro_list(empty, services)
    assert "No Sprint has ended yet. A retro is written when a Sprint ends." in empty.edits[-1][0]

    ended = await _ended(sessions, RETRO_LIST_PAGE_SIZE + 1)
    async with sessions() as session:
        oldest = await session.get(Sprint, ended[0].id)
        oldest.criterion_met = True
        oldest.analysis = {"headline": "Steady."}
        running = await start_sprint(session, success_criteria="Still going")
        await session.commit()
        newest = await session.get(Sprint, ended[-1].id)
        oldest = await session.get(Sprint, ended[0].id)

    message = FakeMessage(501, bot_message=True)
    await render_retro_list(message, services)

    text, markup = message.edits[-1]
    labels = button_texts(markup)
    assert "page 1/2" in text
    assert labels[0] == (
        f"{newest.number} · {newest.planned_start_date:%d.%m}–{newest.planned_end_date:%d.%m} · —"
    )
    assert labels[RETRO_LIST_PAGE_SIZE:] == ["Next ▶", "↩️ Menu"]
    assert not any(running.number in label for label in labels)

    await _tap(message, services, "Next ▶")
    text, markup = message.edits[-1]
    assert "page 2/2" in text
    oldest_label = (
        f"{oldest.number} · {oldest.planned_start_date:%d.%m}–{oldest.planned_end_date:%d.%m} "
        "· ✅ 🔎"
    )
    assert button_texts(markup) == [oldest_label, "◀ Previous", "↩️ Menu"]

    await _tap(message, services, oldest_label)
    text, markup = message.edits[-1]
    assert f"Sprint {oldest.number} retro" in text
    assert button_texts(markup)[-2:] == ["↩️ Back", "↩️ Menu"]

    # The mark is changed on the same screen, and the way back still leads to page 2.
    await _tap(message, services, "❌ Not met")
    await _tap(message, services, "↩️ Back")
    text, markup = message.edits[-1]
    assert "page 2/2" in text
    assert button_texts(markup)[0].endswith("· ❌ 🔎")


def _record(number: int, *, met: str, capacity: float | str, minutes: int | None) -> dict:
    record = {field: number for field in SUMMED}
    record.update(link=f"[Sprint {number} retro](retro:{number})", criteria_met=met)
    record["capacity"] = capacity
    del record["tracked_minutes"], record["actions_with_time"]
    if minutes is not None:
        record.update(tracked_minutes=minutes, actions_with_time=1)
    return record


def test_rt_ask_013_sums_and_means_are_worked_out_by_the_code() -> None:
    """RT-ASK-013 — tests/brd/retro.feature"""
    records = {
        "1": _record(1, met="yes", capacity=10, minutes=60),
        "2": _record(2, met="no", capacity="off", minutes=None),
        "3": _record(3, met="not marked", capacity=20, minutes=30),
        "9": {"error": "No Sprint has the number 9."},
    }
    for number, (taken, done) in zip(("1", "2", "3"), ((10, 5), (20, 5), (30, 20)), strict=True):
        records[number].update(effort_taken=taken, effort_done=done)

    total, mean = aggregate(records, "sum"), aggregate(records, "mean")

    plain = set(SUMMED) - {
        "effort_taken", "effort_done", "capacity", "criteria_met",
        "tracked_minutes", "actions_with_time",
    }
    for field in plain:
        assert (total["values"][field], mean["values"][field]) == (6, 2), field
    assert (total["values"]["effort_taken"], mean["values"]["effort_taken"]) == (60, 20)
    # Met is 1 and Not met 0; a Sprint with no mark is not in that number.
    assert (total["values"]["criteria_met"], mean["values"]["criteria_met"]) == (1, 0.5)
    assert (total["values"]["capacity"], mean["values"]["capacity"]) == (30, 15)
    assert (total["values"]["tracked_minutes"], mean["values"]["tracked_minutes"]) == (90, 45)
    assert mean["counted_over"] == {
        "criteria_met": 2, "capacity": 2, "tracked_minutes": 2, "actions_with_time": 2,
    }
    # The share finished is the finished total over the taken total, never a sum of shares.
    assert total["done_share_percent"] == mean["done_share_percent"] == 50
    assert mean["sprint_count"] == 3
    assert mean["sprints"] == [records[n]["link"] for n in ("1", "2", "3")]
    assert mean["errors"] == {"9": "No Sprint has the number 9."}


async def test_rt_ask_014_a_date_finds_the_sprint_whose_days_it_falls_in(
    sessions, monkeypatch
) -> None:
    """RT-ASK-014 — tests/brd/retro.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Planned", stage="sprint", effort_points=2)
        sprint = await start_sprint(
            session, success_criteria="Ship v2", start_date=date(2025, 9, 1), length_days=14
        )
        sprint.actual_started_at = datetime(2025, 9, 1, 6, tzinfo=UTC)
        with monkeypatch.context() as patched:
            patched.setattr(
                planning_use_cases, "utcnow", lambda: datetime(2025, 9, 12, 12, tzinfo=UTC)
            )
            await finish_sprint(session)
        await session.commit()
    context = AgentContext(
        owner_id=42,
        timezone="Europe/Istanbul",
        query_runner=None,  # type: ignore[arg-type]
        history=None,  # type: ignore[arg-type]
        sessions=sessions,
    )
    [find] = [tool for tool in RETRO_AGENT.read_tools(context) if tool.name == "get_retro_number"]

    async def on(day: str) -> dict:
        return await find.run(
            ToolCall(id="find", name="get_retro_number", arguments_json=json.dumps({"date": day}))
        )

    found = await on("2025-09-10")
    assert (found["number"], found["id"]) == (sprint.number, sprint.id)
    assert found["ran"] == "2025-09-01 – 2025-09-12"
    assert await on("2025-09-13") == {"error": "No Sprint was running on 2025-09-13."}

    # A Sprint that runs today has no retro yet, and says so.
    async with sessions() as session:
        running = await start_sprint(session, success_criteria="Ship v3")
        await session.commit()
    today = await on(running.planned_start_date.isoformat())
    assert running.number in today["error"] and "retro is written when it ends" in today["error"]
