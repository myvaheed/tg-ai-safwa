"""Calendar executions, rather than open Card rows, consume the plan's capacity."""

from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from ui_harness import FakeMessage, services_for

from safwa.features.cards.hooks import TODAY_OVERLOAD_HOOK, today_overload_request
from safwa.features.cards.model import Card, CardStage, TodayDay
from safwa.features.cards.use_cases import (
    create_card,
    finish_action,
    move_card,
    record_today_morning,
    update_card_fields,
)
from safwa.features.home.dashboard import dashboard_text
from safwa.features.planning.agent import sprint_now
from safwa.features.planning.api import (
    plan_load,
    refresh_schedule_commitment,
    sprint_counts,
    sprint_metrics,
)
from safwa.features.planning.closing import sprint_closing
from safwa.features.planning.model import Sprint, SprintCommitment
from safwa.features.planning.telegram.plan import render_plan
from safwa.features.planning.telegram.sprint import render_sprint
from safwa.features.planning.use_cases import start_sprint
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.retro.analysis import SprintColumn, overview_text, shares_text
from safwa.features.retro.records import aggregate, sprint_record
from safwa.features.retro.telegram import retro_text
from safwa.features.schedules.api import assign_first, remaining_occurrences
from safwa.features.schedules.hooks import compile_revision
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.hooks.registry import HookRegistry


async def scheduled_card(session, *, count=1, stage="sprint", effort_points=5):
    card = await create_card(
        session,
        kind="action",
        title="Train",
        stage=stage,
        effort_points=effort_points,
        schedule=f"{count} times a day",
    )
    card.schedule_record.rule = {"kind": "quota", "period": "day", "count": count}
    card.schedule_record.status = "ready"
    await assign_first(session, card)
    await refresh_schedule_commitment(session, card)
    return card


async def test_profile_length_weights_the_plan_and_its_ui(sessions, effort_on):
    """PL-REPEAT-031 — tests/brd/planning.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 3)
        await set_profile_field(session, ProfileField.CAPACITY_EFFORT_POINTS, 10)
        card = await scheduled_card(session)
        load = await plan_load(session, [card])
        assert (load.actions, load.effort, load.counts) == (3, 15, {card.id: 3})
        await session.commit()
    assert "Planned: 3 Actions" in await sprint_now(SimpleNamespace(sessions=sessions))
    services = services_for(sessions)
    message = FakeMessage(100, bot_message=False)
    await render_sprint(message, services)
    assert "3 Actions · 15 EP" in message.answers[-1]
    assert "Above configured capacity" in message.answers[-1]
    await render_plan(message, services)
    assert "× 3" in message.answers[-1]
    assert ">15</td>" in message.answers[-1]
    async with sessions() as session:
        await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 7)
        load = await plan_load(session, [await session.get(Card, card.id)])
        assert (load.actions, load.effort) == (7, 35)


async def test_an_action_joining_a_running_sprint_counts_only_the_days_left(sessions):
    """PL-REPEAT-031 — tests/brd/planning.feature"""
    async with sessions() as session:
        await scheduled_card(session, stage="sprint")
        sprint = await start_sprint(session, success_criteria="Train", length_days=7)
        # The Sprint is on its fifth day: three days are left, today among them.
        sprint.planned_start_date -= timedelta(days=4)
        sprint.planned_end_date -= timedelta(days=4)
        late = await scheduled_card(session, stage="sprint")
        commitment = await session.scalar(
            select(SprintCommitment).where(SprintCommitment.card_id == late.id)
        )
        assert (commitment.scope_kind, commitment.planned_count) == ("added", 3)
        assert (await plan_load(session, [late])).counts == {late.id: 3}


async def test_successor_keeps_reserved_scope_and_frozen_unit_effort(sessions, effort_on):
    """PL-REPEAT-032 — tests/brd/planning.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        card = await scheduled_card(session, stage="today")
        sprint = await start_sprint(session, success_criteria="Train", length_days=3)
        original = await session.scalar(
            select(SprintCommitment).where(SprintCommitment.card_id == card.id)
        )
        original.key_action = True
        await update_card_fields(session, card.id, {"effort_points": 8, "tracked_mins": 30})
        result = await finish_action(session, card.id)
        successor = await session.get(Card, result.successor_ids[0])
        rows = list(await session.scalars(select(SprintCommitment).order_by(SprintCommitment.id)))
        assert [row.planned_count for row in rows] == [1, 2]
        assert all(
            row.scope_kind == "initial" and row.effort_snapshot == 5 and row.key_action
            for row in rows
        )
        assert successor.effort_points == 8 and successor.tracked_mins is None
        await update_card_fields(
            session, successor.id, {"blocked": True, "blocked_description": "Rest"}
        )
        assert await sprint_metrics(session, sprint.id) == {
            "committed": 15,
            "added": 0,
            "removed": 0,
            "completed": 5,
        }
        counts = await sprint_counts(session, sprint.id)
        assert (counts["committed"], counts["added"], counts["completed"]) == (3, 0, 1)
        metrics = (
            (await session.execute(text("SELECT * FROM ai_current_sprint_metrics")))
            .mappings()
            .one()
        )
        assert (
            metrics["committed"],
            metrics["completed"],
            metrics["actions_committed"],
            metrics["actions_completed"],
        ) == (15, 5, 3, 1)
        stats = await sprint_closing(session, sprint, timezone="Europe/Istanbul")
        assert (
            stats.planned,
            stats.finished,
            stats.remaining,
            stats.key_total,
            stats.key_finished,
        ) == (3, 1, 2, 3, 1)
        assert stats.blocked == 2
        assert (
            stats.by_category["none"].count,
            stats.by_category["none"].effort,
            stats.by_category["none"].done_effort,
        ) == (3, 15, 5)
        assert (stats.minutes, stats.timed, stats.timed_effort) == (30, 1, 5)
        await move_card(session, successor.id, CardStage.BACKLOG)
        assert (await sprint_metrics(session, sprint.id))["removed"] == 10
        await move_card(session, successor.id, CardStage.SPRINT)
        assert (await sprint_metrics(session, sprint.id))["removed"] == 0
        await session.commit()
    message = FakeMessage(101, bot_message=False)
    await render_sprint(message, services_for(sessions))
    assert "Remaining · 2" in message.answers[-1]
    assert "× 2 · 10 EP" in message.answers[-1]


async def test_today_load_and_morning_plan_do_not_double_count_copies(sessions, effort_on):
    """PL-REPEAT-033 — tests/brd/planning.feature"""
    async with sessions() as session:
        card = await scheduled_card(session, count=5, stage="today")
        sprint = await start_sprint(session, success_criteria="Train", length_days=3)
        day = utcnow().astimezone(ZoneInfo("Europe/Istanbul")).date()
        before = await today_overload_request(session, [])
        assert "Today holds 25 EP" in before and "5 executions × 5 EP" in before
        assert await record_today_morning(session) == [card]
        successor_id = (await finish_action(session, card.id)).successor_ids[0]
        successor = await session.get(Card, successor_id)
        assert await record_today_morning(session) == []
        assert list(await session.scalars(select(TodayDay.planned_count))) == [5]
        load = await plan_load(session, [successor], start_date=day, end_date=day)
        assert (load.actions, load.effort) == (4, 20)
        after = await today_overload_request(session, [])
        assert "Today holds 25 EP" in after and "5 EP of it is finished" in after
        home = await dashboard_text(session, {})
        assert "Planned: 4 Actions · 20 EP" in home and "× 4" in home
        stats = await sprint_closing(session, sprint, timezone="Europe/Istanbul")
        assert (stats.days[0].planned, stats.days[0].done) == (5, 1)
        assert (stats.planned, stats.remaining, stats.done) == (15, 14, 5)


@pytest.mark.parametrize("effort", [None, 5])
async def test_added_series_counts_future_executions_once(sessions, effort):
    """PL-REPEAT-032 — tests/brd/planning.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Ordinary", stage="sprint", effort_points=1)
        sprint = await start_sprint(session, success_criteria="Train", length_days=3)
        added = await scheduled_card(session, count=2, stage="today", effort_points=effort)
        assert (await sprint_counts(session, sprint.id))["added"] == 6
        await finish_action(session, added.id)
        counts = await sprint_counts(session, sprint.id)
        assert (counts["committed"], counts["added"], counts["completed"]) == (1, 6, 1)
        assert counts["unestimated"] == (6 if effort is None else 0)


async def test_unknown_schedule_is_persisted_and_excludes_exact_ratios(sessions, effort_on):
    """PL-REPEAT-034 — tests/brd/planning.feature"""
    async with sessions() as session:
        card = await create_card(
            session,
            kind="action",
            title="Train",
            stage="sprint",
            effort_points=5,
            schedule="repeat",
        )
        card.schedule_record.status = "ready"
        card.schedule_record.rule = {"kind": "after_completion"}
        await assign_first(session, card)
        sprint = await start_sprint(session, success_criteria="Train", length_days=3)
        sprint_id = sprint.id
        await session.commit()
        session.expire_all()
        sprint = await session.get(Sprint, sprint_id)
        row = await session.scalar(select(SprintCommitment))
        assert row.planned_count is None
        assert (await sprint_counts(session, sprint_id))["committed"] == 0
        sql = (
            (await session.execute(text("SELECT * FROM ai_current_sprint_metrics")))
            .mappings()
            .one()
        )
        assert (sql["actions_committed"], sql["committed"], sql["unknown_schedules"]) == (0, 0, 1)
        stats = await sprint_closing(session, sprint, timezone="Europe/Istanbul")
        assert stats.unknown_schedules == 1
        sprint.retro = stats.as_record()
        record = sprint_record(sprint, effort_tracking=True)
        assert "effort_taken" not in record and record["unknown_schedules"] == 1
        assert "Lower bounds" in aggregate({sprint.number: record}, "sum")["planned_totals"]
        assert "%" not in retro_text(sprint, stats, effort_tracking=True)
        column = SprintColumn(sprint.number, stats.first_day, stats.last_day, "Train", None, stats)
        assert "%" not in overview_text([column], effort_tracking=True)
        assert "without planned shares" in shares_text(
            [column], "Category", "Categories", "by_category", effort_tracking=True
        )


async def test_compilation_refreshes_open_reservation_and_today_hook(sessions, effort_on):
    """PL-REPEAT-034 — tests/brd/planning.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Train", stage="today", effort_points=5, schedule="daily"
        )
        sprint = await start_sprint(session, success_criteria="Train", length_days=3)
        id, card_id, sprint_id = card.schedule_id, card.id, sprint.id
        await session.commit()

    async def compile(*args):
        return {"kind": "quota", "period": "day", "count": 4}, None

    context = SimpleNamespace(
        sessions=sessions,
        resources=SimpleNamespace(schedule_compiler=SimpleNamespace(compile=compile)),
    )
    sink = bind_committed(
        sessions, HookRegistry.of((TODAY_OVERLOAD_HOOK,), owners=frozenset({"cards"}))
    )
    try:
        await compile_revision(id, context)
        await sink.drain()
    finally:
        await sink.close()
    async with sessions() as session:
        assert list(await session.scalars(select(Cue.hook))) == [TODAY_OVERLOAD_HOOK.name]
        assert (await sprint_counts(session, sprint_id))["committed"] == 12
        assert "Today holds 20 EP" in await today_overload_request(session, [])
        card = await session.get(Card, card_id)
        await update_card_fields(session, card.id, {"schedule": None})
        assert (await sprint_counts(session, sprint_id))["committed"] == 1


async def test_fixed_window_and_overdue_current_execution(sessions):
    """PL-REPEAT-031 — tests/brd/planning.feature"""
    async with sessions() as session:
        card = await scheduled_card(session)
        at = utcnow() + timedelta(days=2)
        card.schedule_record.rule = {
            "kind": "fixed",
            "timing": {"schedule_kind": "once", "anchor_at": at.isoformat()},
        }
        await assign_first(session, card)
        day = utcnow().astimezone(ZoneInfo("Europe/Istanbul")).date()
        # Outside the window the appointment plans nothing, and the Action itself counts once.
        assert await remaining_occurrences(session, card, day, day) == 1
        assert await remaining_occurrences(session, card, day, day + timedelta(days=2)) == 1
        assert (
            await remaining_occurrences(
                session, card, day + timedelta(days=3), day + timedelta(days=3)
            )
            == 1
        )


async def test_weekly_quota_counts_its_share_and_ep_off_still_counts_executions(sessions):
    """PL-REPEAT-031 — tests/brd/planning.feature"""
    async with sessions() as session:
        card = await scheduled_card(session, count=3, effort_points=None)
        card.schedule_record.rule["period"] = "week"
        await assign_first(session, card)
        day = utcnow().astimezone(ZoneInfo("Europe/Istanbul")).date()
        # One day of three a week rounds to none, and the Action itself still counts once.
        load = await plan_load(session, [card], start_date=day, end_date=day)
        assert (load.actions, load.unestimated, load.unknown_schedules) == (1, 1, 0)
        week = await plan_load(
            session, [card], start_date=day, end_date=day + timedelta(days=6 - day.weekday())
        )
        assert week.actions == max(1, round(3 * (7 - day.weekday()) / 7))
        await start_sprint(session, success_criteria="Train", length_days=7)
        await session.commit()
    message = FakeMessage(102, bot_message=False)
    await render_sprint(message, services_for(sessions))
    assert "Taken <b>3 Actions</b>" in message.answers[-1] and "EP" not in message.answers[-1]


async def test_schedule_change_refreshes_only_open_work(sessions):
    """PL-REPEAT-034 — tests/brd/planning.feature"""
    async with sessions() as session:
        card = await scheduled_card(session)
        sprint = await start_sprint(session, success_criteria="Train", length_days=3)
        successor = await session.get(
            Card, (await finish_action(session, card.id)).successor_ids[0]
        )
        await update_card_fields(session, successor.id, {"schedule": "two times a day"})
        assert (await sprint_counts(session, sprint.id))["unknown_schedules"] == 1
        successor.schedule_record.status = "ready"
        successor.schedule_record.rule = {"kind": "quota", "period": "day", "count": 2}
        await assign_first(session, successor)
        await refresh_schedule_commitment(session, successor)
        assert await sprint_metrics(session, sprint.id) == {
            "committed": 35,
            "added": 0,
            "removed": 0,
            "completed": 5,
        }
        assert (await sprint_counts(session, sprint.id))["committed"] == 7


async def test_extra_execution_outside_reservation_is_added_once(sessions):
    """PL-REPEAT-032 — tests/brd/planning.feature"""
    async with sessions() as session:
        card = await scheduled_card(session)
        card.schedule_record.rule = {
            "kind": "fixed",
            "timing": {
                "schedule_kind": "interval",
                "anchor_at": (utcnow() + timedelta(minutes=1)).isoformat(),
                "interval_minutes": 3 * 1440,
            },
        }
        await assign_first(session, card)
        sprint = await start_sprint(session, success_criteria="Train", length_days=2)
        successor = await session.get(
            Card, (await finish_action(session, card.id)).successor_ids[0]
        )
        assert successor.effective_stage == "backlog"
        await move_card(session, successor.id, CardStage.TODAY)
        assert (await sprint_counts(session, sprint.id))["added"] == 1
        await finish_action(session, successor.id)
        counts = await sprint_counts(session, sprint.id)
        assert (counts["committed"], counts["added"], counts["completed"]) == (1, 1, 2)
        assert await sprint_metrics(session, sprint.id) == {
            "committed": 5,
            "added": 5,
            "removed": 0,
            "completed": 10,
        }
