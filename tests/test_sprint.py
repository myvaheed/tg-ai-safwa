from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.bootstrap.modules import MODULES
from safwa.constants import SPRINT_LENGTH_DAYS
from safwa.features.cards.model import CardStage
from safwa.features.cards.use_cases import create_card as create_domain_card
from safwa.features.cards.use_cases import (
    finish_action,
    move_card,
    toggle_card_value,
    update_card_fields,
)
from safwa.features.planning.model import Sprint
from safwa.features.planning.use_cases import (
    expire_due_sprint,
    finish_sprint,
    set_sprint_success_criteria,
    sprint_metrics,
    start_sprint,
)
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.use_cases import delete_reminder
from safwa.features.values.use_cases import create_value
from safwa.features.workspace_mutator.state import workspace_context
from safwa.foundation.workspace import Workspace
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.foundation.clock import SystemClock
from tg_agent_shell.foundation.errors import DomainError


async def create_card(session, **overrides):
    payload = {"title": "Action", "kind": "action", "stage": "backlog", "effort_points": 3}
    payload.update(overrides)
    return await create_domain_card(session, **payload)


async def plan_one(session, **overrides):
    """The one Action a Sprint needs before it can start."""
    payload = {"title": "Planned", "stage": "sprint"}
    payload.update(overrides)
    return await create_card(session, **payload)


async def test_pl_criteria_003_a_sprint_starts_with_words_and_with_work(sessions):
    """PL-CRITERIA-003 — tests/brd/planning.feature"""
    async with sessions() as session:
        with pytest.raises(DomainError):
            await set_sprint_success_criteria(session, "   ")

        # Words but no work.
        with pytest.raises(DomainError, match="at least one Action"):
            await start_sprint(session, success_criteria="Ship v2")

        await plan_one(session)

        # Work but no words.
        with pytest.raises(DomainError):
            await start_sprint(session, success_criteria="   ")

        sprint = await start_sprint(session, success_criteria="Ship v2")

        assert sprint.success_criteria == "Ship v2"
        assert (await session.get(Workspace, 1)).active_sprint_id == sprint.id


async def test_pl_criteria_004_success_criteria_outlive_the_sprint(sessions):
    """PL-CRITERIA-004 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)
        await set_sprint_success_criteria(session, "Ship v2")
        await start_sprint(session, success_criteria="Ship v2")
        await finish_sprint(session, reason="finished_early")
        await session.commit()

        assert (await session.get(Workspace, 1)).sprint_success_criteria == "Ship v2"
        context = await workspace_context(session)

    assert "No Sprint is running" in context.state
    assert "Draft Success criteria for the next one: Ship v2" in context.state
    assert "Today Actions:" not in context.state


async def test_pl_start_005_a_sprint_runs_the_length_settings_asked_for(sessions):
    """PL-START-005 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)
        await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 7, clock=SystemClock())

        sprint = await start_sprint(session, success_criteria="Ship v2")

        assert (sprint.planned_end_date - sprint.planned_start_date).days == 6
        # A Sprint starts on the owner's day, which is not the UTC one all evening.
        workspace = await session.get(Workspace, 1)
        local = sprint.actual_started_at.astimezone(ZoneInfo(workspace.timezone))
        assert sprint.planned_start_date == local.date()


async def test_pl_start_005_the_default_length_is_the_constant(sessions):
    """PL-START-005 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)

        sprint = await start_sprint(session, success_criteria="Ship v2")

        assert (
            sprint.planned_end_date - sprint.planned_start_date
        ).days == SPRINT_LENGTH_DAYS - 1


async def test_pl_start_005_numbering_follows_the_highest_number_ever_used(sessions):
    """PL-START-005 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)
        first = await start_sprint(session, success_criteria="Ship v2")
        await finish_sprint(session, reason="finished_early")
        second = await start_sprint(session, success_criteria="Ship v3")
        await finish_sprint(session, reason="finished_early")

        third = await start_sprint(session, success_criteria="Ship v4")

        assert [first.number, second.number, third.number] == [1, 2, 3]


async def test_pl_scope_006_a_sprint_commits_to_what_is_planned_at_the_effort_it_has_then(
    sessions,
):
    """PL-SCOPE-006 — tests/brd/planning.feature"""
    async with sessions() as session:
        goal = await create_card(session, title="Be fit", kind="goal", effort_points=None)
        small = await create_card(session, title="Small", stage="sprint", effort_points=3)
        await create_card(
            session, title="Big", stage="today", effort_points=5, parent_id=goal.id
        )
        sprint = await start_sprint(session, success_criteria="Ship v2")

        assert (await sprint_metrics(session, sprint.id))["committed"] == 8

        await update_card_fields(session, small.id, {"effort_points": 8})

        # What the Sprint took on is what it was worth then, not what it is worth now.
        assert (await sprint_metrics(session, sprint.id))["committed"] == 8


async def test_pl_warn_011_a_sprint_warns_the_owner_before_it_ends(sessions):
    """PL-WARN-011 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)
        sprint = await start_sprint(session, success_criteria="Ship v2")
        reminders = list(await session.scalars(select(Reminder).order_by(Reminder.next_fire_at)))

        assert [reminder.sprint_id for reminder in reminders] == [sprint.id, sprint.id]
        # Both fire at the clock the Sprint started at, one day apart.
        assert reminders[1].next_fire_at - reminders[0].next_fire_at == timedelta(days=1)
        assert reminders[0].next_fire_at.timetz() == sprint.actual_started_at.timetz()
        assert "ends tomorrow" in reminders[0].instruction
        assert "ends today" in reminders[1].instruction

        await finish_sprint(session, reason="finished_early")

        # Both warnings went with it, and the hand-over is a Cue rather than a Reminder.
        assert list(await session.scalars(select(Reminder))) == []
        handed = list(await session.scalars(select(Cue)))
        assert len(handed) == 1 and handed[0].text.startswith("Sprint")


async def test_pl_warn_011_a_two_day_sprint_only_warns_on_its_last_day(sessions):
    """PL-WARN-011 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)
        await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 2, clock=SystemClock())
        await start_sprint(session, success_criteria="Ship v2")
        reminders = list(await session.scalars(select(Reminder)))

        assert len(reminders) == 1
        assert "ends today" in reminders[0].instruction


async def test_a_sprints_end_warnings_belong_to_safwa(sessions):
    """RM-SYSTEM-022 — tests/brd/reminders.feature"""
    async with sessions() as session:
        await plan_one(session)
        await start_sprint(session, success_criteria="Ship v2")
        reminders = list(await session.scalars(select(Reminder)))

        assert reminders and all(reminder.system for reminder in reminders)
        # So the owner never sees a trigger they cannot own, and the model never reads one
        # it cannot name: the Sprint that authored them is what removes them.
        for reminder in reminders:
            with pytest.raises(DomainError, match="change it in Settings"):
                await delete_reminder(session, reminder.id)

        await finish_sprint(session, reason="finished_early")
        left = list(await session.scalars(select(Reminder)))
        assert all(reminder.system for reminder in left)


async def test_pl_end_013_a_sprint_expires_only_after_local_midnight_past_its_end(sessions):
    """PL-END-013 — tests/brd/planning.feature"""
    async with sessions() as session:
        action = await create_card(session, title="Ship", stage="sprint")
        sprint = await start_sprint(session, success_criteria="Ship v2")
        # Midnight is the workspace's own midnight, three hours before the UTC one.
        local_midnight = datetime.combine(
            sprint.planned_end_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=ZoneInfo("Europe/Istanbul"),
        )

        assert await expire_due_sprint(session, now=local_midnight - timedelta(minutes=1)) is None

        expired = await expire_due_sprint(session, now=local_midnight)

        assert expired is not None
        assert expired.finish_reason == "expired"
        workspace = await session.get(Workspace, 1)
        assert workspace.active_sprint_id is None
        assert action.effective_stage == "sprint"


async def test_pl_end_015_an_ended_sprint_is_handed_to_safwa(sessions):
    """PL-END-015 — tests/brd/planning.feature"""
    async with sessions() as session:
        done = await create_card(session, title="Shipped", stage="sprint", effort_points=5)
        await create_card(session, title="Still open", stage="today", effort_points=3)
        sprint = await start_sprint(session, success_criteria="Ship v2")
        from safwa.features.cards.model import CardStage
        from safwa.features.cards.use_cases import finish_action

        await finish_action(session, done.id, CardStage.DONE)
        await finish_sprint(session, reason="finished_early")
        await session.commit()

        handed = list(await session.scalars(select(Cue)))
        # Its own end warnings went with it; the hand-over is no Reminder of any kind.
        assert list(await session.scalars(select(Reminder))) == []

    assert len(handed) == 1
    words = handed[0].text
    assert f"Sprint {sprint.number} is over" in words
    assert "the owner closed it" in words
    assert "Success criteria: Ship v2" in words
    assert "committed 8, added 0, removed 0, done 5, cancelled 0" in words
    assert "1 finished, 0 cancelled, 1 still open" in words
    assert "Still open: Still open" in words
    assert f"[Sprint retro](retro:{sprint.id})" in words


async def test_pl_end_015_a_sprint_that_closed_itself_says_so(sessions):
    """PL-END-015 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)
        sprint = await start_sprint(session, success_criteria="Ship v2")
        local_midnight = datetime.combine(
            sprint.planned_end_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=ZoneInfo("Europe/Istanbul"),
        )
        await expire_due_sprint(session, now=local_midnight)
        await session.commit()

        handed = list(await session.scalars(select(Cue)))

    assert len(handed) == 1
    assert "its end date passed" in handed[0].text


async def test_pl_mode_002_no_tool_anywhere_writes_a_sprint(sessions):
    """PL-MODE-002 — tests/brd/planning.feature"""
    tools = {
        contribution.tool.name for module in MODULES for contribution in module.proposals
    }
    entities = {
        contribution.handler.entity for module in MODULES for contribution in module.proposals
    }

    assert "sprint" not in tools
    assert "sprint" not in entities
    # The Sprint is not proposable at all: nothing the model can call reaches one.
    assert not any("sprint" in name for name in tools)


async def test_pl_context_010_safwa_is_handed_the_sprint_and_todays_actions(sessions):
    """PL-CONTEXT-010 — tests/brd/planning.feature"""
    async with sessions() as session:
        await create_card(session, title="Ship it", stage="today")
        await create_card(session, title="Later", stage="backlog")
        sprint = await start_sprint(session, success_criteria="Ship v2")
        await session.commit()

        context = await workspace_context(session)

    assert f"Sprint {sprint.number}: {sprint.planned_start_date} – {sprint.planned_end_date}" in (
        context.state
    )
    assert "Success criteria: Ship v2" in context.state
    assert "Today Actions:" in context.state
    assert "[Ship it](card:1)" in context.state
    assert "Later" not in context.state
    # The five effort figures are not handed over; Safwa reads them when it wants them.
    for word in ("committed", "Committed", "cancelled effort"):
        assert word not in context.state


async def test_board_context_lists_critical_cards_valued_first(sessions):
    async with sessions() as session:
        value = await create_value(session, name="Health", active=True)
        for index in range(11):
            await create_card(session, title=f"Critical {index:02d}", priority="critical")
        valued = await create_card(session, title="Valued", priority="critical")
        await toggle_card_value(session, valued.id, value.id)
        await create_card(session, title="Ordinary", priority="medium")
        await session.commit()

        context = await workspace_context(session)

    listed = [line for line in context.state.splitlines() if line.startswith("- [")]
    assert len(listed) == 10
    assert listed[0].startswith("- [Valued](card:")
    assert "Ordinary" not in context.state


async def test_pl_end_012_finishing_early_leaves_the_work_where_it_is(sessions):
    """PL-END-012 — tests/brd/planning.feature"""
    async with sessions() as session:
        await create_card(session, title="Initial", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        added = await create_card(session, title="Added", stage="today", effort_points=3)

        await finish_sprint(session, reason="finished_early")

        workspace = await session.get(Workspace, 1)
        assert workspace.mode == "planning"
        assert workspace.active_sprint_id is None
        assert added.effective_stage == "today"
        assert (await session.get(Sprint, sprint.id)).finish_reason == "finished_early"
        with pytest.raises(DomainError, match="No Sprint is active"):
            await finish_sprint(session)


async def test_pl_end_014_a_sprint_ending_is_when_the_workspace_is_tidied(sessions):
    """PL-END-014 — tests/brd/planning.feature"""
    from safwa.constants import ARCHIVE_AFTER_SPRINTS
    from safwa.features.cards.model import Card, CardStage
    from safwa.features.cards.use_cases import finish_action

    async with sessions() as session:
        closed = await create_card(session, title="Shipped", stage="sprint", effort_points=2)
        await plan_one(session, title="Carries the Sprints")
        await start_sprint(session, success_criteria="Ship v1")
        await finish_action(session, closed.id, CardStage.DONE)
        await finish_sprint(session)
        await session.commit()

        for index in range(ARCHIVE_AFTER_SPRINTS):
            await start_sprint(session, success_criteria=f"Ship v{index + 2}")
            # The last of them is the one nobody closed.
            if index == ARCHIVE_AFTER_SPRINTS - 1:
                sprint = await session.get(Workspace, 1)
                running = await session.get(Sprint, sprint.active_sprint_id)
                after_midnight = datetime.combine(
                    running.planned_end_date + timedelta(days=1),
                    datetime.min.time(),
                    tzinfo=ZoneInfo("Europe/Istanbul"),
                )
                await expire_due_sprint(session, now=after_midnight)
            else:
                await finish_sprint(session)
            await session.commit()

        assert (await session.get(Card, closed.id)).archived_at is not None


async def test_pl_scope_007_work_that_joins_a_running_sprint_is_counted_apart(sessions):
    """PL-SCOPE-007 — tests/brd/planning.feature"""
    async with sessions() as session:
        initial = await create_card(session, title="Initial", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        # Created straight into Today while the Sprint runs.
        created = await create_card(session, title="Added", stage="today", effort_points=3)
        moved = await create_card(session, title="Moved", effort_points=2)
        await move_card(session, moved.id, CardStage.SPRINT)
        await finish_action(session, initial.id, CardStage.DONE)

        metrics = await sprint_metrics(session, sprint.id)

        assert metrics == {"committed": 5, "added": 5, "removed": 0, "completed": 5, "cancelled": 0}
        assert created.effective_stage == "today"


async def test_pl_scope_009_a_sprint_records_what_each_action_came_to(sessions):
    """PL-SCOPE-009 — tests/brd/planning.feature"""
    async with sessions() as session:
        action = await create_card(session, title="Ship", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        await finish_action(session, action.id, CardStage.DONE)
        assert (await sprint_metrics(session, sprint.id))["completed"] == 5

        await move_card(session, action.id, CardStage.SPRINT)

        # A reopened Action is live again, so its effort must stop counting as completed.
        assert (await sprint_metrics(session, sprint.id))["completed"] == 0
        assert action.effective_stage == CardStage.SPRINT.value

        await finish_action(session, action.id, CardStage.CANCELLED)

        metrics = await sprint_metrics(session, sprint.id)
        assert (metrics["cancelled"], metrics["completed"]) == (5, 0)


async def test_pl_scope_008_returning_to_sprint_scope_cancels_the_earlier_removal(sessions):
    """PL-SCOPE-008 — tests/brd/planning.feature"""
    async with sessions() as session:
        action = await create_card(session, title="Ship", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        await move_card(session, action.id, CardStage.BACKLOG)
        assert (await sprint_metrics(session, sprint.id))["removed"] == 5

        await move_card(session, action.id, CardStage.TODAY)

        # The same effort must not be reported as both removed and selected.
        assert (await sprint_metrics(session, sprint.id))["removed"] == 0
