from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.ai.context import ordered_owner_context, planning_context
from safwa.domain import create_reminder, start_sprint
from safwa.features.profile.api import update_profile
from safwa.features.profile.screens import SETTINGS_FIELDS
from safwa.foundation.errors import DomainError
from safwa.models import Reminder, UserProfile, Workspace
from safwa.reminders import resolve


async def test_explicit_profile_context_is_after_memory_in_the_prompt(sessions) -> None:
    """PS-CONTEXT-001: explicit current Profile values follow inferred memory."""
    async with sessions() as session:
        await update_profile(
            session,
            about_me="Current About Me",
            advisor_instructions="Current Advisor instruction",
        )
        context = await planning_context(session)

    rendered = ordered_owner_context(
        "About me: stale inference\nAdvisor instructions: stale inference",
        context.state,
    )

    assert rendered.index("Persistent memory:") < rendered.index("Current planning state:")
    assert rendered.index("About me: stale inference") < rendered.index("About me: Current About Me")
    assert rendered.index("Advisor instructions: stale inference") < rendered.index(
        "Advisor instructions: Current Advisor instruction"
    )


async def test_profile_rejects_an_undeclared_field_without_changes(sessions) -> None:
    """PS-FIELD-002: unknown fields change neither Profile nor revision."""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        workspace = await session.get(Workspace, 1)
        original_about = profile.about_me
        original_revision = workspace.revision

        with pytest.raises(DomainError, match="Unsupported profile field"):
            await update_profile(session, timezone="Europe/Istanbul")

        assert profile.about_me == original_about
        assert workspace.revision == original_revision


async def test_sprint_length_accepts_2_to_60_days_only(sessions) -> None:
    """PS-SPRINT-LENGTH-003: the inclusive domain is exactly 2 through 60."""
    async with sessions() as session:
        profile = await update_profile(session, sprint_length_days=2)
        assert profile.sprint_length_days == 2
        profile = await update_profile(session, sprint_length_days=60)
        assert profile.sprint_length_days == 60

        with pytest.raises(DomainError, match="between 2 and 60"):
            await update_profile(session, sprint_length_days=1)
        with pytest.raises(DomainError, match="between 2 and 60"):
            await update_profile(session, sprint_length_days=61)

        assert profile.sprint_length_days == 60


async def test_sprint_capacity_accepts_positive_points_or_off(sessions) -> None:
    """PS-CAPACITY-004: capacity is a positive integer or None (`off`)."""
    async with sessions() as session:
        profile = await update_profile(session, capacity_effort_points=1)
        assert profile.capacity_effort_points == 1
        profile = await update_profile(session, capacity_effort_points=None)
        assert profile.capacity_effort_points is None

        with pytest.raises(DomainError, match="positive whole number"):
            await update_profile(session, capacity_effort_points=0)
        with pytest.raises(DomainError, match="positive whole number"):
            await update_profile(session, capacity_effort_points=-1)
        with pytest.raises(DomainError, match="positive whole number"):
            await update_profile(session, capacity_effort_points=1.5)

        assert profile.capacity_effort_points is None


def test_scheduled_profile_clocks_accept_hhmm_or_off() -> None:
    """PS-CLOCK-005: both Settings clocks share the HH:MM/off parser contract."""
    memory_clock = SETTINGS_FIELDS["memory_update_time"].parse
    diary_clock = SETTINGS_FIELDS["diary_time"].parse

    assert memory_clock("00:00") == time(0, 0)
    assert diary_clock("23:59") == time(23, 59)
    assert memory_clock("off") is None
    assert diary_clock("off") is None
    with pytest.raises(ValueError, match="HH:MM"):
        memory_clock("24:00")
    with pytest.raises(ValueError, match="HH:MM"):
        diary_clock("tomorrow")


async def test_diary_reminder_settings_sync_only_the_diary_system_reminder(sessions) -> None:
    """PS-DIARY-006: Diary Settings isolate the Diary System Reminder."""
    async with sessions() as session:
        sprint = await start_sprint(session, success_criteria="Keep the Sprint reminders")
        ordinary = await create_reminder(
            session,
            instruction="Ask whether I want another Diary check-in.",
            schedule=resolve(
                interval_minutes=120,
                now=datetime.now(UTC),
                tz=ZoneInfo("UTC"),
            ),
            tz=ZoneInfo("UTC"),
        )
        sprint_before = {
            reminder.id: (reminder.instruction, reminder.next_fire_at, reminder.version)
            for reminder in await session.scalars(
                select(Reminder).where(Reminder.sprint_id == sprint.id)
            )
        }
        ordinary_before = (ordinary.instruction, ordinary.next_fire_at, ordinary.version)

        await update_profile(
            session,
            diary_time=time(7, 30),
            diary_instructions="Ask about sleep.",
        )
        internal = await session.scalar(
            select(Reminder).where(
                Reminder.system.is_(True),
                Reminder.sprint_id.is_(None),
            )
        )
        assert internal is not None
        assert internal.at_time == time(7, 30)
        assert internal.instruction.endswith("Ask about sleep.")
        assert (ordinary.instruction, ordinary.next_fire_at, ordinary.version) == ordinary_before
        assert {
            reminder.id: (reminder.instruction, reminder.next_fire_at, reminder.version)
            for reminder in await session.scalars(
                select(Reminder).where(Reminder.sprint_id == sprint.id)
            )
        } == sprint_before

        internal_id = internal.id
        await update_profile(session, diary_time=None)

        assert await session.get(Reminder, internal_id) is None
        assert await session.get(Reminder, ordinary.id) is ordinary
        assert len(list(await session.scalars(select(Reminder).where(Reminder.sprint_id == sprint.id)))) == len(
            sprint_before
        )


async def test_profile_update_bumps_workspace_revision_once(sessions) -> None:
    """PS-REVISION-011: one update, including Diary reconciliation, is one revision."""
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        revision = workspace.revision

        await update_profile(
            session,
            diary_time=time(8, 15),
            diary_instructions="Notice energy.",
        )

        assert workspace.revision == revision + 1
