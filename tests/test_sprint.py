from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.bootstrap.modules import MODULES, RECOVERY_HOOKS, REGISTRY
from safwa.features.cards.hard_time import typed_hard_time
from safwa.features.cards.hooks import (
    ENERGY_BALANCE_HOOK,
    ENERGY_CANDIDATES,
    HARD_TIME_HOOK,
    PLAN_CHECK,
    energy_balance_request,
    hard_time_request,
)
from safwa.features.cards.model import CardStage
from safwa.features.cards.use_cases import (
    archive_subtree,
    finish_action,
    move_card,
    update_card_fields,
)
from safwa.features.cards.use_cases import create_card as create_domain_card
from safwa.features.planning.api import SPRINT_ENDED, SPRINT_STARTED
from safwa.features.planning.hooks import (
    SPRINT_EXPIRY_HOOK,
    SPRINT_SUMMARY_HOOK,
    midnight,
    sprint_summary_request,
)
from safwa.features.planning.model import Sprint, next_sprint_number
from safwa.features.planning.use_cases import (
    expire_due_sprint,
    finish_sprint,
    set_sprint_success_criteria,
    sprint_metrics,
    start_sprint,
)
from safwa.features.profile.api import morning_time
from safwa.features.profile.model import SPRINT_LENGTH_DAYS, ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.use_cases import SPRINT_KEY, delete_reminder
from safwa.features.workspace_mutator.state import workspace_context
from safwa.foundation.workspace import Workspace
from tg_agent_shell.cues.initiatives import queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.foundation.changes import Committed, take_changes
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.hooks.contracts import OnCommitted, OnTick, Run, RunContext, Tick
from tg_agent_shell.recovery import recover_startup


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
        await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 7)

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


async def test_pl_start_005_a_sprint_number_says_the_month_it_started_in(sessions):
    """PL-START-005 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)
        first = await start_sprint(session, success_criteria="Ship v2")
        await finish_sprint(session, reason="finished_early")
        second = await start_sprint(session, success_criteria="Ship v3")

        month = f"{first.planned_start_date:%y.%m}"
        assert [first.number, second.number] == [f"{month}-01", f"{month}-02"]


def test_pl_start_005_the_sequence_is_per_month_and_never_reused():
    """PL-START-005 — tests/brd/planning.feature"""
    assert next_sprint_number(date(2026, 9, 1), []) == "26.09-01"
    assert next_sprint_number(date(2026, 9, 30), ["26.09-01"]) == "26.09-02"
    # A deleted Sprint leaves its place taken: the sequence follows the highest used.
    assert next_sprint_number(date(2026, 9, 30), ["26.09-02"]) == "26.09-03"
    # A new month starts its own sequence, and the label sorts by start date as text.
    assert next_sprint_number(date(2026, 10, 1), ["26.09-02"]) == "26.10-01"
    assert sorted(["26.10-01", "26.09-02", "26.09-10"]) == [
        "26.09-02",
        "26.09-10",
        "26.10-01",
    ]


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

        assert [reminder.system_key for reminder in reminders] == [SPRINT_KEY, SPRINT_KEY]
        # Both fire at the clock the Sprint started at, one day apart.
        assert reminders[1].next_fire_at - reminders[0].next_fire_at == timedelta(days=1)
        assert reminders[0].next_fire_at.timetz() == sprint.actual_started_at.timetz()
        assert "ends tomorrow" in reminders[0].instruction
        assert "ends today" in reminders[1].instruction

        take_changes(session.info)
        await finish_sprint(session, reason="finished_early")

        # Both warnings went with it, and the hand-over is the Sprint summary hook's, not a
        # Reminder's.
        assert list(await session.scalars(select(Reminder))) == []
        assert take_changes(session.info) == [Committed(SPRINT_ENDED, sprint.id)]


async def test_pl_warn_011_a_two_day_sprint_only_warns_on_its_last_day(sessions):
    """PL-WARN-011 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)
        await set_profile_field(session, ProfileField.SPRINT_LENGTH_DAYS, 2)
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
            with pytest.raises(DomainError, match="change it in the Profile"):
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


async def test_pl_end_013_the_midnight_check_is_a_daily_hook_that_runs_work_of_its_own(sessions):
    """PL-END-013 — tests/brd/planning.feature"""
    assert SPRINT_EXPIRY_HOOK.on == (OnTick(at=midnight),)
    assert isinstance(SPRINT_EXPIRY_HOOK.effect, Run) and not SPRINT_EXPIRY_HOOK.agent_related
    assert midnight in REGISTRY.hooks.daily_clocks
    async with sessions() as session:
        assert await midnight(session) == time(0, 0)
        await plan_one(session)
        sprint = await start_sprint(session, success_criteria="Ship v2")
        # The planned last day is over: the next midnight passing ends it.
        sprint.planned_end_date = date.today() - timedelta(days=1)
        await session.commit()
        sprint_id = sprint.id

    async def nobody_publishes(text: str, kind: str) -> None:
        raise AssertionError("a hook on a tick has no chat")

    work = RunContext(
        resources=None, still_current=lambda: True, publish=nobody_publishes, sessions=sessions
    )
    await queue_advice(REGISTRY.hooks, sessions, Tick("00:00", midnight), work=work)
    async with sessions() as session:
        assert (await session.get(Workspace, 1)).active_sprint_id is None
        assert (await session.get(Sprint, sprint_id)).finish_reason == "expired"
        # Nothing is owed the queue by the work itself: the ending, handed on, is what speaks.
        assert list(await session.scalars(select(Cue))) == []


async def test_pl_end_013_a_midnight_safwa_slept_through_is_made_up_at_startup(sessions):
    """PL-END-013 — tests/brd/planning.feature"""
    async with sessions() as session:
        await plan_one(session)
        sprint = await start_sprint(session, success_criteria="Ship v2")
        sprint.planned_end_date = date.today() - timedelta(days=1)
        await session.commit()
        sprint_id = sprint.id

    async with sessions() as session:
        await recover_startup(session, RECOVERY_HOOKS)
        await session.commit()

    async with sessions() as session:
        assert (await session.get(Workspace, 1)).active_sprint_id is None
        assert (await session.get(Sprint, sprint_id)).finish_reason == "expired"
        # A Sprint still inside its days is left running by the same start.
        await plan_one(session)
        running = await start_sprint(session, success_criteria="Ship v3")
        await session.commit()
        running_id = running.id
    async with sessions() as session:
        await recover_startup(session, RECOVERY_HOOKS)
        assert (await session.get(Workspace, 1)).active_sprint_id == running_id


async def test_pl_end_015_an_ended_sprint_is_handed_to_safwa(sessions):
    """PL-END-015 — tests/brd/planning.feature"""
    async with sessions() as session:
        done = await create_card(session, title="Shipped", stage="sprint", effort_points=5)
        await create_card(session, title="Still open", stage="today", effort_points=3)
        sprint = await start_sprint(session, success_criteria="Ship v2")
        from safwa.features.cards.use_cases import finish_action

        await finish_action(session, done.id)
        take_changes(session.info)
        await finish_sprint(session, reason="finished_early")
        # Its own end warnings went with it; the hand-over is no Reminder of any kind.
        assert list(await session.scalars(select(Reminder))) == []
        assert take_changes(session.info) == [Committed(SPRINT_ENDED, sprint.id)]
        assert SPRINT_SUMMARY_HOOK.agent_related
        assert SPRINT_SUMMARY_HOOK.on == (OnCommitted(kind=SPRINT_ENDED),)
        # The words are made from the record when the request is about to be said.
        words = await sprint_summary_request(session, [sprint.id])
        assert await sprint_summary_request(session, [sprint.id + 100]) is None
        await session.commit()

    assert words is not None
    assert f"Sprint {sprint.number} is over" in words
    assert "the owner closed it" in words
    assert "Success criteria: Ship v2" in words
    assert "committed 8, added 0, removed 0, done 5" in words
    assert "1 finished, 1 still open" in words
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
        take_changes(session.info)
        await expire_due_sprint(session, now=local_midnight)
        assert take_changes(session.info) == [Committed(SPRINT_ENDED, sprint.id)]
        words = await sprint_summary_request(session, [sprint.id])
        await session.commit()

    assert words is not None and "its end date passed" in words


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
    # The four effort figures are not handed over; Safwa reads them when it wants them.
    for word in ("committed", "Committed", "removed effort"):
        assert word not in context.state


async def test_pl_context_020_todays_actions_are_handed_over_only_while_a_sprint_runs(sessions):
    """PL-CONTEXT-020 — tests/brd/planning.feature"""
    async with sessions() as session:
        today = await create_card(
            session, title="Ship it", stage="today", effort_points=5, priority="critical"
        )
        # Written after it and less important, so only the ranking can order these three.
        low = await create_card(
            session, title="Sometime", stage="today", effort_points=1, priority="low"
        )
        medium = await create_card(
            session, title="Middling", stage="today", effort_points=2, priority="medium"
        )
        # A Hard Time outranks all of it: least important, written last, still first.
        fixed = await create_card(
            session, title="The dentist", stage="today", effort_points=1,
            priority="low", hard_time=await typed_hard_time(session, "09:00"),
        )
        fixed_at = fixed.hard_time_at.astimezone(ZoneInfo("Europe/Istanbul"))
        await create_card(session, title="Later", stage="sprint", effort_points=3)
        await create_domain_card(session, title="The release", kind="goal", stage="today")
        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
        running = (await workspace_context(session)).state

        await finish_sprint(session, reason="finished_early")
        await session.commit()
        planning = (await workspace_context(session)).state

    handed = running.split("Today Actions:")[1]

    # The Actions in Today with the effort each carries, most important first: the Action
    # still in Sprint and the Goal above them are both looked up rather than handed over.
    assert [line for line in handed.splitlines() if line.startswith("- [")] == [
        f"- [The dentist](card:{fixed.id}) effort=1 hard_time={fixed_at:%d.%m %H:%M}",
        f"- [Ship it](card:{today.id}) effort=5",
        f"- [Middling](card:{medium.id}) effort=2",
        f"- [Sometime](card:{low.id}) effort=1",
    ]
    assert "Today Actions:" not in planning
    assert "The release" not in running


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
    from safwa.features.cards.model import Card
    from safwa.features.cards.use_cases import finish_action
    from safwa.features.planning.use_cases import ARCHIVE_AFTER_SPRINTS

    async with sessions() as session:
        closed = await create_card(session, title="Shipped", stage="sprint", effort_points=2)
        await plan_one(session, title="Carries the Sprints")
        await start_sprint(session, success_criteria="Ship v1")
        await finish_action(session, closed.id)
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
        await finish_action(session, initial.id)

        metrics = await sprint_metrics(session, sprint.id)

        assert metrics == {"committed": 5, "added": 5, "removed": 0, "completed": 5}
        assert created.effective_stage == "today"


async def test_pl_scope_009_a_sprint_records_what_each_action_came_to(sessions):
    """PL-SCOPE-009 — tests/brd/planning.feature"""
    async with sessions() as session:
        action = await create_card(session, title="Ship", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        await finish_action(session, action.id)
        assert (await sprint_metrics(session, sprint.id))["completed"] == 5

        await move_card(session, action.id, CardStage.SPRINT)

        # A reopened Action is live again, so its effort must stop counting as completed.
        assert (await sprint_metrics(session, sprint.id))["completed"] == 0
        assert action.effective_stage == CardStage.SPRINT.value


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


async def test_pl_hardtime_021_the_request_names_the_hard_times_the_plan_does_not_hold(sessions):
    """PL-HARDTIME-021 — tests/brd/planning.feature"""
    assert HARD_TIME_HOOK.agent_related
    assert HARD_TIME_HOOK.on == (OnCommitted(kind=SPRINT_STARTED), OnTick(at=morning_time))
    today = datetime.now(ZoneInfo("Europe/Istanbul")).date()

    def on(days: int) -> str:
        # The last minute of the day, so today's has not passed whenever the test runs.
        return f"{today + timedelta(days=days):%d.%m.%Y} 23:59"

    async with sessions() as session:
        async def fixed(title: str, days: int, **overrides):
            return await create_card(
                session, title=title, hard_time=await typed_hard_time(session, on(days)),
                **overrides,
            )

        dentist = await fixed("Dentist", 1)
        # In Planning there is no plan to hold anything.
        assert await hard_time_request(session, [PLAN_CHECK]) is None
        await plan_one(session)
        sprint = await start_sprint(session, success_criteria="Ship v2", length_days=7)
        assert take_changes(session.info) == [Committed(SPRINT_STARTED, sprint.id)]
        await session.commit()

        tax = await fixed("Tax office", 5)
        await fixed("Concert", 20)
        call = await fixed("Call mom", 0, stage="sprint")
        await fixed("Report", 3, stage="sprint")
        await fixed("Gym", 1, stage="today")
        train = await fixed("Missed train", 1)
        train.hard_time_at = utcnow() - timedelta(days=1)
        paid = await fixed("Paid", 2)
        await finish_action(session, paid.id)
        shelved = await fixed("Shelved", 2)
        await finish_action(session, shelved.id)
        await archive_subtree(session, shelved.id)
        await session.commit()

        request = await hard_time_request(session, [PLAN_CHECK])
        assert request is not None
        for line in (
            f"#{dentist.id} «Dentist»: {today + timedelta(days=1):%Y-%m-%d} 23:59, in Backlog",
            f"#{tax.id} «Tax office»: {today + timedelta(days=5):%Y-%m-%d} 23:59, in Backlog",
            f"#{call.id} «Call mom»: {today:%Y-%m-%d} 23:59, in Sprint",
        ):
            assert line in request
        for absent in ("Concert", "Report", "Gym", "Missed train", "Paid", "Shelved"):
            assert absent not in request
        assert "Do not move anything without their answer" in request

        # Taken into the plan before it is said, each is left out; with none left, nothing.
        await move_card(session, dentist.id, CardStage.TODAY)
        await move_card(session, tax.id, CardStage.SPRINT)
        await move_card(session, call.id, CardStage.TODAY)
        await session.commit()
        assert await hard_time_request(session, [PLAN_CHECK]) is None


async def test_pl_energy_022_the_request_names_each_kind_the_sprint_lacks_and_the_backlog_has(
    sessions,
):
    """PL-ENERGY-022 — tests/brd/planning.feature"""
    assert ENERGY_BALANCE_HOOK.agent_related
    assert ENERGY_BALANCE_HOOK.on == (OnCommitted(kind=SPRINT_STARTED),)
    async with sessions() as session:
        # In Planning there is no Sprint to spread anything over.
        assert await energy_balance_request(session, [PLAN_CHECK]) is None
        await plan_one(session, title="Write the report", energy_types={"cognitive"})
        run = await create_card(session, title="Run", energy_types={"physical"})
        swim = await create_card(session, title="Swim", energy_types={"physical"})
        await create_card(session, title="Hike", energy_types={"physical"})
        climb = await create_card(
            session, title="Climb", energy_types={"physical"}, priority="critical"
        )
        help_out = await create_card(session, title="Help out", energy_types={"values"})
        nap = await create_card(session, title="Nap", categories={"rest"})
        # Cognitive is in the Sprint already; a finished one is not open in the Backlog;
        # nothing anywhere carries Social.
        await create_card(session, title="Read a paper", energy_types={"cognitive"})
        walked = await create_card(session, title="Walked", categories={"rest"})
        await finish_action(session, walked.id)
        sprint = await start_sprint(session, success_criteria="Ship v2")
        assert take_changes(session.info) == [Committed(SPRINT_STARTED, sprint.id)]
        await session.commit()

        request = await energy_balance_request(session, [PLAN_CHECK])
        assert request is not None
        assert ENERGY_CANDIDATES == 3
        for line in (
            f"- Physical energy: #{climb.id} «Climb» (Critical), #{run.id} «Run», #{swim.id} «Swim»",
            f"- Values energy: #{help_out.id} «Help out»",
            f"- Rest: #{nap.id} «Nap»",
        ):
            assert line in request
        for absent in ("Hike", "Cognitive", "Social", "Read a paper", "Walked"):
            assert absent not in request
        assert "Do not move anything without their answer" in request

        # Gained by the time it is said, a kind is left out; with nothing missing, nothing.
        await move_card(session, climb.id, CardStage.SPRINT)
        await session.commit()
        request = await energy_balance_request(session, [PLAN_CHECK])
        assert request is not None and "Physical" not in request and "Rest" in request
        await move_card(session, help_out.id, CardStage.SPRINT)
        await move_card(session, nap.id, CardStage.TODAY)
        await session.commit()
        assert await energy_balance_request(session, [PLAN_CHECK]) is None

        # Ended by then, the Sprint has nothing to spread.
        await move_card(session, climb.id, CardStage.BACKLOG)
        await finish_sprint(session)
        await session.commit()
        assert await energy_balance_request(session, [PLAN_CHECK]) is None
