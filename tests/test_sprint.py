from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.ai.context import planning_context
from safwa.constants import SPRINT_LENGTH_DAYS
from safwa.domain import (
    DomainError,
    create_value,
    expire_due_sprint,
    finish_sprint,
    set_sprint_success_criteria,
    start_sprint,
    toggle_card_value,
)
from safwa.domain import create_card as create_domain_card
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.foundation.clock import SystemClock
from safwa.models import Reminder, Workspace


async def create_card(session, **overrides):
    payload = {"title": "Action", "kind": "action", "stage": "backlog", "effort_points": 3}
    payload.update(overrides)
    return await create_domain_card(session, **payload)


async def test_a_sprint_cannot_start_without_success_criteria(sessions):
    async with sessions() as session:
        with pytest.raises(DomainError):
            await start_sprint(session, success_criteria="   ")


async def test_sprint_length_comes_from_settings_and_is_bounded(sessions):
    async with sessions() as session:
        await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 7, clock=SystemClock())
        sprint = await start_sprint(session, success_criteria="Ship v2")
        assert (sprint.planned_end_date - sprint.planned_start_date).days == 6
        assert sprint.success_criteria == "Ship v2"

        with pytest.raises(DomainError):
            await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 1, clock=SystemClock())
        with pytest.raises(DomainError):
            await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 61, clock=SystemClock())


async def test_default_sprint_length_is_the_constant(sessions):
    async with sessions() as session:
        sprint = await start_sprint(session, success_criteria="Ship v2")
        assert (
            sprint.planned_end_date - sprint.planned_start_date
        ).days == SPRINT_LENGTH_DAYS - 1


async def test_starting_a_sprint_schedules_both_end_reminders(sessions):
    async with sessions() as session:
        sprint = await start_sprint(session, success_criteria="Ship v2")
        reminders = list(
            await session.scalars(select(Reminder).order_by(Reminder.next_fire_at))
        )

        assert [reminder.sprint_id for reminder in reminders] == [sprint.id, sprint.id]
        # Both fire at the clock the Sprint started at, one day apart.
        assert reminders[1].next_fire_at - reminders[0].next_fire_at == timedelta(days=1)
        assert reminders[0].next_fire_at.timetz() == sprint.actual_started_at.timetz()
        assert "ends tomorrow" in reminders[0].instruction
        assert "ends today" in reminders[1].instruction

        await finish_sprint(session, reason="finished_early")

        assert await session.scalar(select(Reminder).limit(1)) is None


async def test_a_two_day_sprint_only_warns_on_its_last_day(sessions):
    async with sessions() as session:
        await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 2, clock=SystemClock())
        await start_sprint(session, success_criteria="Ship v2")
        reminders = list(await session.scalars(select(Reminder)))

        assert len(reminders) == 1
        assert "ends today" in reminders[0].instruction


async def test_a_sprint_expires_only_after_local_midnight_past_its_end(sessions):
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


async def test_planning_context_names_the_sprint_and_today_actions(sessions):
    async with sessions() as session:
        await create_card(session, title="Ship it", stage="today")
        await create_card(session, title="Later", stage="backlog")
        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()

        context = await planning_context(session)

    assert "Success criteria: Ship v2" in context.state
    assert "Today Actions:" in context.state
    assert "[Ship it](card:1)" in context.state
    assert "Later" not in context.state


async def test_planning_context_asks_for_a_sprint_and_hides_today(sessions):
    async with sessions() as session:
        await create_card(session, title="Ship it", stage="today")
        await set_sprint_success_criteria(session, "Ship v2")
        await session.commit()

        context = await planning_context(session)

    assert "No Sprint is running" in context.state
    assert "Draft Success criteria for the next one: Ship v2" in context.state
    assert "Today Actions:" not in context.state


async def test_planning_context_lists_critical_cards_valued_first(sessions):
    async with sessions() as session:
        value = await create_value(session, name="Health", active=True)
        for index in range(11):
            await create_card(session, title=f"Critical {index:02d}", priority="critical")
        valued = await create_card(session, title="Valued", priority="critical")
        await toggle_card_value(session, valued.id, value.id)
        await create_card(session, title="Ordinary", priority="medium")
        await session.commit()

        context = await planning_context(session)

    listed = [line for line in context.state.splitlines() if line.startswith("- [")]
    assert len(listed) == 10
    assert listed[0].startswith("- [Valued](card:")
    assert "Ordinary" not in context.state
