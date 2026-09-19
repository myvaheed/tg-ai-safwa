from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from sqlalchemy import select

from safwa.bootstrap.modules import REGISTRY
from safwa.features.diary.hooks import DIARY_HOOK, DIARY_REQUEST, diary_request
from safwa.features.profile.api import diary_time, morning_time, summary_time
from safwa.features.profile.hooks import (
    DAILY_SUMMARY_HOOK,
    DAILY_SUMMARY_REQUEST,
    daily_summary_request,
)
from safwa.features.profile.model import (
    DIARY_TIME_DEFAULT,
    MORNING_TIME_DEFAULT,
    SUMMARY_TIME_DEFAULT,
    ProfileField,
    UserProfile,
)
from safwa.features.profile.telegram.screens import PROFILE_FIELDS
from safwa.features.profile.use_cases import (
    profile_field,
    set_hook_switch,
    set_profile_field,
)
from safwa.features.reminders.model import Reminder
from safwa.features.workspace_mutator.state import workspace_context
from safwa.foundation.workspace import Workspace
from tg_agent_shell.ai.messages import ordered_owner_context
from tg_agent_shell.cues.background import tick as cue_tick
from tg_agent_shell.cues.initiatives import queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_hook_cue
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.hooks.contracts import OnTick, Tick


class FrozenClock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment.astimezone(UTC)


async def test_explicit_profile_context_is_after_memory_in_the_prompt(sessions) -> None:
    """PS-CONTEXT-001 — tests/brd/profile.feature"""
    async with sessions() as session:
        await set_profile_field(
            session, ProfileField.ABOUT_ME, "Current About Me"
        )
        await set_profile_field(
            session,
            ProfileField.ADVISOR_INSTRUCTIONS,
            "Current Advisor instruction",
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
                session, profile_field("timezone"), "Europe/Istanbul"
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
            session, ProfileField.SPRINT_LENGTH_DAYS, 2
        )
        assert profile.sprint_length_days == 2
        profile = await set_profile_field(
            session, ProfileField.SPRINT_LENGTH_DAYS, 60
        )
        assert profile.sprint_length_days == 60

        for rejected in (1, 61):
            with pytest.raises(DomainError, match="between 2 and 60"):
                await set_profile_field(
                    session, ProfileField.SPRINT_LENGTH_DAYS, rejected
                )

        assert profile.sprint_length_days == 60


async def test_sprint_capacity_accepts_positive_points_or_off(sessions) -> None:
    """PS-CAPACITY-004 — tests/brd/profile.feature"""
    async with sessions() as session:
        profile = await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, 1
        )
        assert profile.capacity_effort_points == 1
        profile = await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, None
        )
        assert profile.capacity_effort_points is None

        profile = await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, 12.5
        )
        assert profile.capacity_effort_points == 12.5
        await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, None
        )

        for rejected in (0, -1):
            with pytest.raises(DomainError, match="positive number"):
                await set_profile_field(
                    session, ProfileField.CAPACITY_EFFORT_POINTS, rejected
                )

        assert profile.capacity_effort_points is None


def test_scheduled_profile_clocks_accept_hhmm_and_refuse_off() -> None:
    """PS-CLOCK-005 — tests/brd/profile.feature"""
    diary_clock = PROFILE_FIELDS["diary_time"].parse
    summary_clock = PROFILE_FIELDS["summary_time"].parse

    assert diary_clock("00:00") == time(0, 0)
    assert diary_clock("23:59") == time(23, 59)
    assert summary_clock("20:00") == time(20, 0)
    for refused in (diary_clock, summary_clock):
        with pytest.raises(ValueError, match="HH:MM"):
            refused("off")
        with pytest.raises(ValueError, match="HH:MM"):
            refused("24:00")
    with pytest.raises(ValueError, match="HH:MM"):
        diary_clock("tomorrow")


async def test_ps_morning_016_the_morning_time_is_a_clock_the_daily_hooks_read(sessions) -> None:
    """PS-MORNING-016 — tests/brd/profile.feature"""
    parse = PROFILE_FIELDS["morning_time"].parse
    assert parse("07:30") == time(7, 30)
    for refused in ("off", "24:00", "morning"):
        with pytest.raises(ValueError, match="HH:MM"):
            parse(refused)
    # The morning checks name the Profile's reader, not a constant.
    assert morning_time in REGISTRY.hooks.daily_clocks
    async with sessions() as session:
        assert await morning_time(session) == time.fromisoformat(MORNING_TIME_DEFAULT)
        with pytest.raises(DomainError, match="clock time"):
            await set_profile_field(session, ProfileField.MORNING_TIME, None)
        await set_profile_field(session, ProfileField.MORNING_TIME, time(7, 30))
        await session.commit()
    async with sessions() as session:
        assert await morning_time(session) == time(7, 30)


async def test_a_scheduled_clock_field_refuses_a_value_that_is_not_a_time(sessions) -> None:
    """PS-CLOCK-005 — tests/brd/profile.feature"""
    async with sessions() as session:
        for field in (ProfileField.DIARY_TIME, ProfileField.SUMMARY_TIME):
            with pytest.raises(DomainError, match="clock time"):
                await set_profile_field(session, field, "22:00")


async def test_ps_diary_006_the_diary_nudge_reads_the_diary_time_and_prompt_live(sessions) -> None:
    """PS-DIARY-006 — tests/brd/profile.feature"""
    assert DIARY_HOOK.agent_related and DIARY_HOOK.on == (OnTick(at=diary_time),)
    async with sessions() as session:
        assert await diary_time(session) == time.fromisoformat(DIARY_TIME_DEFAULT)
        # Off is refused: the nudge is switched in the Profile, like the morning checks.
        with pytest.raises(DomainError, match="clock time"):
            await set_profile_field(session, ProfileField.DIARY_TIME, None)
        assert await diary_request(session, ["22:00"]) == DIARY_REQUEST

        await set_profile_field(session, ProfileField.DIARY_TIME, time(7, 30))
        await set_profile_field(session, ProfileField.DIARY_INSTRUCTIONS, "Ask about sleep.")
        assert await diary_time(session) == time(7, 30)
        assert await diary_request(session, ["07:30"]) == f"{DIARY_REQUEST} Ask about sleep."
        # No Reminder row stands behind it any more, so none is written or changed.
        assert list(await session.scalars(select(Reminder))) == []


async def test_ps_summary_014_the_daily_summary_reads_the_summary_time_and_shares_the_turn(sessions) -> None:
    """PS-SUMMARY-014 — tests/brd/profile.feature"""
    assert DAILY_SUMMARY_HOOK.agent_related
    assert DAILY_SUMMARY_HOOK.on == (OnTick(at=summary_time),)
    assert {diary_time, summary_time} <= set(REGISTRY.hooks.daily_clocks)
    async with sessions() as session:
        assert await summary_time(session) == time.fromisoformat(SUMMARY_TIME_DEFAULT)
        with pytest.raises(DomainError, match="clock time"):
            await set_profile_field(session, ProfileField.SUMMARY_TIME, None)
        await set_profile_field(session, ProfileField.SUMMARY_TIME, time(22, 0))
        assert await summary_time(session) == time(22, 0)
        assert await daily_summary_request(session, ["22:00"]) == DAILY_SUMMARY_REQUEST
        await session.commit()

    # Both fall due at 22:00: two requests owed at once, said as one.
    hooks = REGISTRY.hooks
    await queue_advice(hooks, sessions, Tick("22:00", diary_time))
    await queue_advice(hooks, sessions, Tick("22:00", summary_time))
    said: list[str] = []

    async def gate() -> bool:
        return True

    async def speak(event_id: str, text: str) -> bool:
        said.append(text)
        return True

    async def delivered(event_id: str) -> bool:
        return False

    async def prepare(hook: str, payload: list) -> str | None:
        return await hooks.prepare(sessions, hook, payload)

    assert await cue_tick(sessions, gate=gate, speak=speak, delivered=delivered, prepare=prepare) is True
    assert said == [f"{DIARY_REQUEST}\n\n{DAILY_SUMMARY_REQUEST}"]
    async with sessions() as session:
        assert list(await session.scalars(select(Cue))) == []


async def test_profile_update_bumps_workspace_revision_once(sessions) -> None:
    """PS-REVISION-011 — tests/brd/profile.feature"""
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        revision = workspace.revision

        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(8, 15)
        )

        assert workspace.revision == revision + 1


async def test_ag_hook_038_switching_a_hook_off_drops_its_pending_request_for_good(sessions) -> None:
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    async with sessions() as session:
        await add_hook_cue(session, hook="cards.blocker", items=[3])
        await add_hook_cue(session, hook="other.hook", items=[4])
        await set_hook_switch(session, "cards.blocker", on=False)
        await session.commit()
    async with sessions() as session:
        assert [cue.hook for cue in await session.scalars(select(Cue))] == ["other.hook"]
        # A change handed on late, after the switch was read as on, wrote while it was off.
        await add_hook_cue(session, hook="cards.blocker", items=[3])
        await session.commit()
    async with sessions() as session:
        await set_hook_switch(session, "cards.blocker", on=True)
        await session.commit()
    async with sessions() as session:
        assert [cue.hook for cue in await session.scalars(select(Cue))] == ["other.hook"]
        assert (await session.get(UserProfile, 1)).disabled_hooks == []
        # Pressed again on a stale screen, it is no flip, and a legitimate request stays.
        await add_hook_cue(session, hook="cards.blocker", items=[5])
        await set_hook_switch(session, "cards.blocker", on=True)
        await session.commit()
    async with sessions() as session:
        assert sorted(cue.hook for cue in await session.scalars(select(Cue))) == ["cards.blocker", "other.hook"]
