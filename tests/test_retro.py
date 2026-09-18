"""The retro screen: what a Sprint leaves behind once it has closed."""

from __future__ import annotations

import pytest
from ui_harness import FakeMessage, button_texts, services_for

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
from safwa.features.retro.telegram import open_retro
from safwa.features.values.use_cases import create_value
from tg_agent_shell.foundation.errors import DomainError


async def test_rt_open_001_a_sprint_that_ended_keeps_a_screen_of_its_own(sessions) -> None:
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


async def test_rt_stats_003_the_retro_adds_the_sprint_up_from_its_record(sessions) -> None:
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
