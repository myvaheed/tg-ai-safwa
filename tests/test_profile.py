from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.bootstrap.modules import RECOVERY_HOOKS, REGISTRY
from safwa.features.cards.use_cases import create_card
from safwa.features.planning.use_cases import start_sprint
from safwa.features.profile.api import morning_time
from safwa.features.profile.model import (
    MORNING_TIME_DEFAULT,
    SUMMARY_TIME_DEFAULT,
    ProfileField,
    UserProfile,
)
from safwa.features.profile.telegram.screens import PROFILE_FIELDS
from safwa.features.profile.use_cases import (
    DIARY_TRIGGER,
    SUMMARY_REMINDER_INSTRUCTION,
    SUMMARY_TRIGGER,
    profile_field,
    set_hook_switch,
    set_profile_field,
)
from safwa.features.reminders.background import tick
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.schedule import resolve
from safwa.features.reminders.use_cases import SPRINT_KEY, create_reminder
from safwa.features.workspace_mutator.state import workspace_context
from safwa.foundation.workspace import Workspace
from tg_agent_shell.ai.messages import ordered_owner_context
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import merge_hook_cue
from tg_agent_shell.foundation.clock import SystemClock
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.recovery import recover_startup


class FrozenClock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment.astimezone(UTC)


async def test_explicit_profile_context_is_after_memory_in_the_prompt(sessions) -> None:
    """PS-CONTEXT-001 — tests/brd/profile.feature"""
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
        context = await workspace_context(session)

    rendered = ordered_owner_context(
        "About me: stale inference\nAdvisor instructions: stale inference",
        context.state,
    )

    assert rendered.index("Persistent memory:") < rendered.index("Current workspace state:")
    assert rendered.index("About me: stale inference") < rendered.index("About me: Current About Me")
    assert rendered.index("Advisor instructions: stale inference") < rendered.index(
        "Advisor instructions: Current Advisor instruction"
    )


async def test_profile_rejects_an_undeclared_field_without_changes(sessions) -> None:
    """PS-FIELD-002 — tests/brd/profile.feature"""
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
    """PS-FIELD-002 — tests/brd/profile.feature"""
    assert {field.value for field in ProfileField} == set(PROFILE_FIELDS)


async def test_sprint_length_accepts_2_to_60_days_only(sessions) -> None:
    """PS-SPRINT-LENGTH-003 — tests/brd/profile.feature"""
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
    """PS-CAPACITY-004 — tests/brd/profile.feature"""
    async with sessions() as session:
        profile = await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, 1, clock=SystemClock()
        )
        assert profile.capacity_effort_points == 1
        profile = await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, None, clock=SystemClock()
        )
        assert profile.capacity_effort_points is None

        profile = await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, 12.5, clock=SystemClock()
        )
        assert profile.capacity_effort_points == 12.5
        await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, None, clock=SystemClock()
        )

        for rejected in (0, -1):
            with pytest.raises(DomainError, match="positive number"):
                await set_profile_field(
                    session, ProfileField.CAPACITY_EFFORT_POINTS, rejected, clock=SystemClock()
                )

        assert profile.capacity_effort_points is None


def test_scheduled_profile_clocks_accept_hhmm_or_off() -> None:
    """PS-CLOCK-005 — tests/brd/profile.feature"""
    memory_clock = PROFILE_FIELDS["memory_update_time"].parse
    diary_clock = PROFILE_FIELDS["diary_time"].parse
    summary_clock = PROFILE_FIELDS["summary_time"].parse

    assert memory_clock("00:00") == time(0, 0)
    assert diary_clock("23:59") == time(23, 59)
    assert summary_clock("20:00") == time(20, 0)
    assert memory_clock("off") is None
    assert diary_clock("off") is None
    assert summary_clock("off") is None
    with pytest.raises(ValueError, match="HH:MM"):
        memory_clock("24:00")
    with pytest.raises(ValueError, match="HH:MM"):
        diary_clock("tomorrow")


async def test_ps_morning_016_the_morning_time_is_a_clock_the_daily_hooks_read(sessions) -> None:
    """PS-MORNING-016 — tests/brd/profile.feature"""
    parse = PROFILE_FIELDS["morning_time"].parse
    assert parse("07:30") == time(7, 30)
    for refused in ("off", "24:00", "morning"):
        with pytest.raises(ValueError, match="HH:MM"):
            parse(refused)
    # The shell's daily tick reads the Profile, not a constant.
    assert REGISTRY.hooks.tick_time is morning_time
    async with sessions() as session:
        assert await morning_time(session) == time.fromisoformat(MORNING_TIME_DEFAULT)
        with pytest.raises(DomainError, match="clock time"):
            await set_profile_field(session, ProfileField.MORNING_TIME, None, clock=SystemClock())
        await set_profile_field(session, ProfileField.MORNING_TIME, time(7, 30), clock=SystemClock())
        await session.commit()
    async with sessions() as session:
        assert await morning_time(session) == time(7, 30)


async def test_a_scheduled_clock_field_refuses_a_value_that_is_not_a_time(sessions) -> None:
    """PS-CLOCK-005 — tests/brd/profile.feature"""
    async with sessions() as session:
        for field in (
            ProfileField.MEMORY_UPDATE_TIME,
            ProfileField.DIARY_TIME,
            ProfileField.SUMMARY_TIME,
        ):
            with pytest.raises(DomainError, match="clock time"):
                await set_profile_field(session, field, "22:00", clock=SystemClock())


async def test_diary_reminder_settings_sync_only_the_diary_system_reminder(sessions) -> None:
    """PS-DIARY-006 — tests/brd/profile.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Planned", stage="sprint", effort_points=3)
        await start_sprint(session, success_criteria="Keep the Sprint reminders")
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
                select(Reminder).where(Reminder.system_key == SPRINT_KEY)
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
            select(Reminder).where(Reminder.system_key == DIARY_TRIGGER)
        )
        assert internal is not None
        assert internal.at_time == time(7, 30)
        assert internal.instruction.endswith("Ask about sleep.")
        assert (ordinary.instruction, ordinary.next_fire_at, ordinary.version) == ordinary_before
        assert {
            reminder.id: (reminder.instruction, reminder.next_fire_at, reminder.version)
            for reminder in await session.scalars(
                select(Reminder).where(Reminder.system_key == SPRINT_KEY)
            )
        } == sprint_before

        internal_id = internal.id
        await set_profile_field(session, ProfileField.DIARY_TIME, None, clock=SystemClock())

        assert await session.get(Reminder, internal_id) is None
        assert await session.get(Reminder, ordinary.id) is ordinary
        assert len(list(await session.scalars(select(Reminder).where(Reminder.system_key == SPRINT_KEY)))) == len(
            sprint_before
        )


async def test_the_diary_trigger_is_scheduled_from_the_clock_it_is_given(sessions) -> None:
    """PS-DIARY-012 — tests/brd/profile.feature"""
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        workspace.timezone = "Europe/Istanbul"
        zone = ZoneInfo("Europe/Istanbul")
        late = datetime(2026, 8, 21, 23, 50, tzinfo=zone)

        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(7, 30), clock=FrozenClock(late)
        )
        internal = await session.scalar(
            select(Reminder).where(Reminder.system_key == DIARY_TRIGGER)
        )

        assert internal.next_fire_at.astimezone(zone) == datetime(
            2026, 8, 22, 7, 30, tzinfo=zone
        )


async def test_startup_reconciles_the_diary_trigger_before_rebuilding_reminders(
    sessions,
) -> None:
    """PS-DIARY-013 — tests/brd/profile.feature"""
    zone = ZoneInfo("Europe/Istanbul")
    async with sessions() as session:
        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(22, 0), clock=SystemClock()
        )
        internal = await session.scalar(
            select(Reminder).where(Reminder.system_key == DIARY_TRIGGER)
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
            select(Reminder).where(Reminder.system_key == DIARY_TRIGGER)
        )
    assert reconciled is not None
    assert reconciled.next_fire_at.replace(tzinfo=UTC).astimezone(zone).time() == time(22, 0)
    assert reconciled.next_fire_at.replace(tzinfo=UTC) > datetime.now(UTC)


async def test_ps_summary_014_the_summary_time_is_its_own_reminder(sessions) -> None:
    """PS-SUMMARY-014 — tests/brd/profile.feature"""
    zone = ZoneInfo("Europe/Istanbul")
    evening = datetime(2026, 8, 21, 19, 0, tzinfo=zone)
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        workspace.timezone = "Europe/Istanbul"
        profile = await session.get(UserProfile, 1)
        assert profile.summary_time == time.fromisoformat(SUMMARY_TIME_DEFAULT)

        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(20, 0), clock=FrozenClock(evening)
        )
        diary = await session.scalar(
            select(Reminder).where(Reminder.system_key == DIARY_TRIGGER)
        )
        diary_before = (diary.instruction, diary.next_fire_at, diary.version)

        await set_profile_field(
            session, ProfileField.SUMMARY_TIME, time(20, 0), clock=FrozenClock(evening)
        )
        summary = await session.scalar(
            select(Reminder).where(Reminder.system_key == SUMMARY_TRIGGER)
        )
        assert summary is not None and summary.system
        assert (summary.at_time, summary.instruction) == (time(20, 0), SUMMARY_REMINDER_INSTRUCTION)
        assert (diary.instruction, diary.next_fire_at, diary.version) == diary_before
        summary_id = summary.id
        await session.commit()

    # Both fall due at 20:00, and one tick writes them down as one request.
    assert await tick(sessions, tz=zone, now=datetime(2026, 8, 21, 20, 1, tzinfo=zone)) is True
    async with sessions() as session:
        cues = list(await session.scalars(select(Cue)))
        assert len(cues) == 1
        assert cues[0].text.startswith("2 Reminders triggered.")
        assert SUMMARY_REMINDER_INSTRUCTION in cues[0].text

        await set_profile_field(session, ProfileField.SUMMARY_TIME, None, clock=SystemClock())
        assert await session.get(Reminder, summary_id) is None
        assert await session.scalar(
            select(Reminder).where(Reminder.system_key == DIARY_TRIGGER)
        ) is not None


async def test_profile_update_bumps_workspace_revision_once(sessions) -> None:
    """PS-REVISION-011 — tests/brd/profile.feature"""
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        revision = workspace.revision

        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(8, 15), clock=SystemClock()
        )

        assert workspace.revision == revision + 1


async def test_ag_hook_038_switching_a_hook_off_drops_its_pending_request_for_good(sessions) -> None:
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    async with sessions() as session:
        await merge_hook_cue(session, hook="cards.blocker", items=[3])
        await merge_hook_cue(session, hook="other.hook", items=[4])
        await set_hook_switch(session, "cards.blocker", on=False)
        await session.commit()
    async with sessions() as session:
        assert [cue.hook for cue in await session.scalars(select(Cue))] == ["other.hook"]
        # A change handed on late, after the switch was read as on, wrote while it was off.
        await merge_hook_cue(session, hook="cards.blocker", items=[3])
        await session.commit()
    async with sessions() as session:
        await set_hook_switch(session, "cards.blocker", on=True)
        await session.commit()
    async with sessions() as session:
        assert [cue.hook for cue in await session.scalars(select(Cue))] == ["other.hook"]
        assert (await session.get(UserProfile, 1)).disabled_hooks == []
        # Pressed again on a stale screen, it is no flip, and a legitimate request stays.
        await merge_hook_cue(session, hook="cards.blocker", items=[5])
        await set_hook_switch(session, "cards.blocker", on=True)
        await session.commit()
    async with sessions() as session:
        assert sorted(cue.hook for cue in await session.scalars(select(Cue))) == ["cards.blocker", "other.hook"]
