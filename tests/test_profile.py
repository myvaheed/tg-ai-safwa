from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.ai.context import board_context, ordered_owner_context
from safwa.bootstrap.modules import RECOVERY_HOOKS
from safwa.domain import start_sprint
from safwa.features.profile.model import ProfileField
from safwa.features.profile.screens import SETTINGS_FIELDS
from safwa.features.profile.use_cases import profile_field, set_profile_field
from safwa.features.reminders.schedule import resolve
from safwa.features.reminders.use_cases import create_reminder
from safwa.foundation.clock import SystemClock
from safwa.foundation.errors import DomainError
from safwa.models import Reminder, UserProfile, Workspace
from safwa.recovery import recover_startup


class FrozenClock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment.astimezone(UTC)


async def test_explicit_profile_context_is_after_memory_in_the_prompt(sessions) -> None:
    """PS-CONTEXT-001 — tests/brd/profile_settings.feature"""
    async with sessions() as session:
        await set_profile_field(
            session, ProfileField.ABOUT_ME, "Current About Me", clock=SystemClock()
        )
        await set_profile_field(
            session,
            ProfileField.ADVISOR_INSTRUCTIONS,
            "Current Advisor instruction",
            clock=SystemClock(),
        )
        context = await board_context(session)

    rendered = ordered_owner_context(
        "About me: stale inference\nAdvisor instructions: stale inference",
        context.state,
    )

    assert rendered.index("Persistent memory:") < rendered.index("Current board state:")
    assert rendered.index("About me: stale inference") < rendered.index("About me: Current About Me")
    assert rendered.index("Advisor instructions: stale inference") < rendered.index(
        "Advisor instructions: Current Advisor instruction"
    )


async def test_profile_rejects_an_undeclared_field_without_changes(sessions) -> None:
    """PS-FIELD-002 — tests/brd/profile_settings.feature"""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        workspace = await session.get(Workspace, 1)
        original_about = profile.about_me
        original_revision = workspace.revision

        with pytest.raises(DomainError, match="Unsupported profile field"):
            await set_profile_field(
                session, profile_field("timezone"), "Europe/Istanbul", clock=SystemClock()
            )

        assert profile.about_me == original_about
        assert workspace.revision == original_revision


def test_the_declared_fields_are_exactly_the_editable_settings() -> None:
    """PS-FIELD-002 — tests/brd/profile_settings.feature"""
    assert {field.value for field in ProfileField} == set(SETTINGS_FIELDS)


async def test_sprint_length_accepts_2_to_60_days_only(sessions) -> None:
    """PS-SPRINT-LENGTH-003 — tests/brd/profile_settings.feature"""
    async with sessions() as session:
        profile = await set_profile_field(
            session, ProfileField.SPRINT_LENGTH_DAYS, 2, clock=SystemClock()
        )
        assert profile.sprint_length_days == 2
        profile = await set_profile_field(
            session, ProfileField.SPRINT_LENGTH_DAYS, 60, clock=SystemClock()
        )
        assert profile.sprint_length_days == 60

        for rejected in (1, 61):
            with pytest.raises(DomainError, match="between 2 and 60"):
                await set_profile_field(
                    session, ProfileField.SPRINT_LENGTH_DAYS, rejected, clock=SystemClock()
                )

        assert profile.sprint_length_days == 60


async def test_sprint_capacity_accepts_positive_points_or_off(sessions) -> None:
    """PS-CAPACITY-004 — tests/brd/profile_settings.feature"""
    async with sessions() as session:
        profile = await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, 1, clock=SystemClock()
        )
        assert profile.capacity_effort_points == 1
        profile = await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, None, clock=SystemClock()
        )
        assert profile.capacity_effort_points is None

        for rejected in (0, -1, 1.5):
            with pytest.raises(DomainError, match="positive whole number"):
                await set_profile_field(
                    session, ProfileField.CAPACITY_EFFORT_POINTS, rejected, clock=SystemClock()
                )

        assert profile.capacity_effort_points is None


def test_scheduled_profile_clocks_accept_hhmm_or_off() -> None:
    """PS-CLOCK-005 — tests/brd/profile_settings.feature"""
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


async def test_a_scheduled_clock_field_refuses_a_value_that_is_not_a_time(sessions) -> None:
    """PS-CLOCK-005 — tests/brd/profile_settings.feature"""
    async with sessions() as session:
        for field in (ProfileField.MEMORY_UPDATE_TIME, ProfileField.DIARY_TIME):
            with pytest.raises(DomainError, match="clock time"):
                await set_profile_field(session, field, "22:00", clock=SystemClock())


async def test_diary_reminder_settings_sync_only_the_diary_system_reminder(sessions) -> None:
    """PS-DIARY-006 — tests/brd/profile_settings.feature"""
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

        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(7, 30), clock=SystemClock()
        )
        await set_profile_field(
            session, ProfileField.DIARY_INSTRUCTIONS, "Ask about sleep.", clock=SystemClock()
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
        await set_profile_field(session, ProfileField.DIARY_TIME, None, clock=SystemClock())

        assert await session.get(Reminder, internal_id) is None
        assert await session.get(Reminder, ordinary.id) is ordinary
        assert len(list(await session.scalars(select(Reminder).where(Reminder.sprint_id == sprint.id)))) == len(
            sprint_before
        )


async def test_the_diary_trigger_is_scheduled_from_the_clock_it_is_given(sessions) -> None:
    """PS-DIARY-012 — tests/brd/profile_settings.feature"""
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        workspace.timezone = "Europe/Istanbul"
        zone = ZoneInfo("Europe/Istanbul")
        late = datetime(2026, 8, 21, 23, 50, tzinfo=zone)

        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(7, 30), clock=FrozenClock(late)
        )
        internal = await session.scalar(
            select(Reminder).where(Reminder.system.is_(True), Reminder.sprint_id.is_(None))
        )

        assert internal.next_fire_at.astimezone(zone) == datetime(
            2026, 8, 22, 7, 30, tzinfo=zone
        )


async def test_startup_reconciles_the_diary_trigger_before_rebuilding_reminders(
    sessions,
) -> None:
    """PS-DIARY-013 — tests/brd/profile_settings.feature"""
    zone = ZoneInfo("Europe/Istanbul")
    async with sessions() as session:
        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(22, 0), clock=SystemClock()
        )
        internal = await session.scalar(
            select(Reminder).where(Reminder.system.is_(True), Reminder.sprint_id.is_(None))
        )
        # The process was down while the workspace moved zones, so every stored wall-clock
        # firing is now the wrong instant.
        internal.next_fire_at = datetime(2020, 1, 1, 22, 0, tzinfo=ZoneInfo("UTC"))
        await session.commit()

    async with sessions() as session:
        await recover_startup(session, RECOVERY_HOOKS)
        await session.commit()

    async with sessions() as session:
        reconciled = await session.scalar(
            select(Reminder).where(Reminder.system.is_(True), Reminder.sprint_id.is_(None))
        )
    assert reconciled is not None
    assert reconciled.next_fire_at.replace(tzinfo=UTC).astimezone(zone).time() == time(22, 0)
    assert reconciled.next_fire_at.replace(tzinfo=UTC) > datetime.now(UTC)


async def test_profile_update_bumps_workspace_revision_once(sessions) -> None:
    """PS-REVISION-011 — tests/brd/profile_settings.feature"""
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        revision = workspace.revision

        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(8, 15), clock=SystemClock()
        )

        assert workspace.revision == revision + 1
