"""Regressions in Sprint effort corrections and completion accounting."""

import pytest
from sqlalchemy import select, text
from ui_harness import FakeMessage, services_for

from safwa.features.cards.model import CardStage, effort_label
from safwa.features.cards.use_cases import (
    create_card,
    finish_action,
    move_card,
    update_card_fields,
)
from safwa.features.planning.api import sprint_counts, sprint_metrics
from safwa.features.planning.closing import RetroStatistics, sprint_closing
from safwa.features.planning.model import SprintCommitment
from safwa.features.planning.telegram.sprint import render_sprint
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field


@pytest.mark.parametrize(("original", "corrected"), [(5, 8), (None, 3), (5, None)])
async def test_correcting_finished_effort_updates_actuals_but_keeps_plan(
    sessions, effort_on, original, corrected,
):
    """PL-SCOPE-009 — tests/brd/planning.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        card = await create_card(
            session, kind="action", title="Shipped", stage="sprint", effort_points=original,
            categories={"work"}, energy_types={"cognitive"},
        )
        sprint = await start_sprint(session, success_criteria="Ship")
        await finish_action(session, card.id, tracked_mins=60)
        await update_card_fields(session, card.id, {"effort_points": corrected})
        metrics = await sprint_metrics(session, sprint.id)
        assert metrics["committed"] == (original or 0)
        assert metrics["completed"] == (corrected or 0)
        expected_missing = int(original is None or corrected is None)
        assert (await sprint_counts(session, sprint.id))["unestimated"] == expected_missing
        row = (await session.execute(text("SELECT * FROM ai_current_sprint_metrics"))).mappings().one()
        assert (row["completed"], row["unestimated_actions"]) == (corrected or 0, expected_missing)
        stats = await sprint_closing(session, sprint, timezone="Europe/Istanbul")
        assert (stats.done, stats.timed_effort) == (corrected or 0, corrected or 0)
        for bucket in (
            stats.by_category["work"], stats.by_energy["cognitive"],
            stats.by_category_energy["work"]["cognitive"],
        ):
            assert (bucket.effort, bucket.done_effort, bucket.timed_effort) == (
                original or 0, corrected or 0, corrected or 0,
            )
        await session.commit()

    message = FakeMessage(101, bot_message=False)
    await render_sprint(message, services_for(sessions), view="done")
    assert f"Done <b>{effort_label(corrected or 0)} EP</b>" in message.answers[-1]
    assert f"· {effort_label(corrected)} EP" in message.answers[-1]

    async with sessions() as session:
        sprint = await finish_sprint(session)
        frozen = RetroStatistics.from_record(sprint.retro)
        assert (frozen.done, frozen.unestimated) == (corrected or 0, expected_missing)
        await update_card_fields(session, card.id, {"effort_points": 13})
        assert (await sprint_metrics(session, sprint.id))["completed"] == frozen.done
        assert (await sprint_counts(session, sprint.id))["unestimated"] == frozen.unestimated


@pytest.mark.parametrize("removed", [False, True])
async def test_finishing_from_backlog_is_in_sprint_and_retro(sessions, effort_on, removed):
    """PL-SCOPE-009 — tests/brd/planning.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Plan", stage="sprint", effort_points=2)
        card = await create_card(
            session, kind="action", title="Unexpected work",
            stage="sprint" if removed else "backlog", effort_points=5,
        )
        sprint = await start_sprint(session, success_criteria="Deliver")
        if removed:
            await move_card(session, card.id, CardStage.BACKLOG)
        await finish_action(session, card.id)
        counts = await sprint_counts(session, sprint.id)
        metrics = await sprint_metrics(session, sprint.id)
        assert (counts["completed"], counts["removed"]) == (1, 0)
        assert (metrics["completed"], metrics["removed"]) == (5, 0)
        assert counts["added"] == (0 if removed else 1)
        stats = await sprint_closing(session, sprint, timezone="Europe/Istanbul")
        assert (stats.finished, stats.remaining, stats.done) == (1, 1, 5)


async def test_reopening_into_backlog_removes_work_from_remaining_scope(sessions, effort_on):
    """PL-SCOPE-009 — tests/brd/planning.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Ship", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship")
        await finish_action(session, card.id)
        await move_card(session, card.id, CardStage.BACKLOG)
        stats = await sprint_closing(session, sprint, timezone="Europe/Istanbul")
        assert (stats.finished, stats.remaining, stats.removed) == (0, 0, 5)


async def test_finishing_removed_repeat_reuses_scope_for_scheduled_successor(
    sessions, effort_on,
):
    """PL-REPEAT-032 — tests/brd/planning.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Train", stage="today", effort_points=5,
            schedule="once a day", schedule_rule={"kind": "quota", "period": "day", "count": 1},
        )
        sprint = await start_sprint(session, success_criteria="Train", length_days=3)
        row = await session.scalar(select(SprintCommitment))
        row.key_action = True
        await move_card(session, card.id, CardStage.BACKLOG)
        await finish_action(session, card.id)
        metrics = await sprint_metrics(session, sprint.id)
        counts = await sprint_counts(session, sprint.id)
        view = (await session.execute(text("SELECT * FROM ai_current_sprint_metrics"))).mappings().one()
        assert metrics == {"committed": 15, "added": 0, "removed": 0, "completed": 5}
        assert (counts["committed"], counts["removed"], counts["completed"]) == (3, 0, 1)
        assert (view["removed"], view["actions_removed"]) == (0, 0)
        stats = await sprint_closing(session, sprint, timezone="Europe/Istanbul")
        assert (stats.remaining, stats.key_total, stats.key_finished) == (2, 3, 1)


async def test_finishing_new_backlog_repeat_reserves_future_executions(sessions, effort_on):
    """PL-REPEAT-032 — tests/brd/planning.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Plan", stage="sprint", effort_points=2)
        card = await create_card(
            session, kind="action", title="Unplanned repeat", effort_points=5,
            schedule="Daily", schedule_rule={"kind": "quota", "period": "day", "count": 1},
        )
        sprint = await start_sprint(session, success_criteria="Train", length_days=3)
        await finish_action(session, card.id)
        metrics = await sprint_metrics(session, sprint.id)
        assert (metrics["added"], metrics["completed"]) == (15, 5)
        counts = await sprint_counts(session, sprint.id)
        assert (counts["added"], counts["completed"]) == (3, 1)
        stats = await sprint_closing(session, sprint, timezone="Europe/Istanbul")
        assert (stats.remaining, stats.finished, stats.added) == (3, 1, 15)
