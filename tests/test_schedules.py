"""Schedule compilation, calendar quotas and independent observation series."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from llm_gateway import CompletionTurn, ToolCall
from safwa.features.cards.model import Card, CardStage
from safwa.features.cards.use_cases import (
    create_card,
    delete_one_card,
    finish_action,
    move_card,
    toggle_card_check,
)
from safwa.features.checks.use_cases import (
    create_check,
    delete_check,
    resolve_check,
    update_check_fields,
)
from safwa.features.schedules.agent import (
    ACTION_LIMIT_QUESTION,
    COMPILER_PROMPT,
    UNREADABLE_QUESTION,
    ScheduleCompiler,
    scheduled_tool,
)
from safwa.features.schedules.api import (
    ACTION_DAILY_EXECUTIONS_MAX,
    get_scheduled,
    set_schedule,
)
from safwa.features.schedules.rules import next_slot, period_start
from tg_agent_shell.ai.sql import DEFAULT_CHAR_BUDGET
from tg_agent_shell.foundation.errors import DomainError


async def test_daily_quota_opens_one_instance_and_counts_finished_facts(sessions):
    """SCH-QUOTA-001 — tests/brd/schedules.feature"""
    async with sessions() as session:
        first = await create_card(
            session, kind="action", title="Water", schedule="five times a day", schedule_rule={"kind": "quota", "period": "day", "count": 5}
        )
        start = first.period_start
        current = first
        for _ in range(5):
            result = await finish_action(session, current.id)
            current = await session.get(Card, result.successor_ids[0])
        assert current.period_start > start
        assert current.effective_stage == "backlog"
        day = start.astimezone(ZoneInfo("Europe/Istanbul")).date()
        result = await get_scheduled(session, day, day, "card")
        assert result["items"][0]["range"]["done"] == 5
        assert result["items"][0]["range"]["remaining"] == 0


async def test_a_partly_covered_week_plans_its_share_of_the_quota(sessions):
    """SCH-WINDOW-002 — tests/brd/schedules.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Stretch?", schedule="three times a week", schedule_rule={"kind": "quota", "period": "week", "count": 3})
        today = check.schedule_record.submitted_at.astimezone(ZoneInfo("Europe/Istanbul")).date()
        monday = today + timedelta(days=7 - today.weekday())
        tuesday, wednesday, sunday = (monday + timedelta(days=n) for n in (1, 2, 6))
        late = (await get_scheduled(session, wednesday, sunday, "check"))["items"][0]["range"]
        early = (await get_scheduled(session, monday, tuesday, "check"))["items"][0]["range"]
        assert (late["planned"], early["planned"]) == (2, 1)
        assert "partial" not in late


async def test_missed_is_an_answer_and_scheduled_checks_stay_independent(sessions):
    """SCH-CHECK-003 — tests/brd/schedules.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Straight posture?", schedule="twice a day", schedule_rule={"kind": "quota", "period": "day", "count": 2})
        action = await create_card(session, kind="action", title="Walk")
        with pytest.raises(DomainError, match="independent"):
            await toggle_card_check(session, action.id, check.id)
        _, successor = await resolve_check(session, check.id, "missed")
        assert successor is not None
        day = check.period_start.astimezone(ZoneInfo("Europe/Istanbul")).date()
        report = await get_scheduled(session, day, day, "check")
        period = report["items"][0]["range"]
        assert (period["done"], period["passed"], period["missed"], period["remaining"]) == (
            1,
            0,
            1,
            1,
        )
        plain = await create_check(session, title="Shoes?")
        await toggle_card_check(session, action.id, plain.id)
        with pytest.raises(DomainError, match="independent"):
            await update_check_fields(session, plain.id, {"schedule": "daily"})


def test_local_calendar_week_and_dst_do_not_use_utc_days():
    """SCH-ZONE-006 — tests/brd/schedules.feature"""
    at = datetime(2026, 3, 29, 23, 30, tzinfo=UTC)
    assert period_start(at, "week", ZoneInfo("Europe/Berlin")) == datetime(
        2026, 3, 29, 22, tzinfo=UTC
    )


async def test_late_quota_answer_belongs_to_today_without_carrying_yesterdays_quota(sessions):
    """SCH-QUOTA-001 — tests/brd/schedules.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Stretch?", schedule="twice a day", schedule_rule={"kind": "quota", "period": "day", "count": 2})
        today = check.period_start
        check.period_start = today - timedelta(days=1)
        _, successor = await resolve_check(session, check.id, "passed")
        assert check.period_start == today and successor.period_start == today
        day = today.astimezone(ZoneInfo("Europe/Istanbul")).date()
        period = (await get_scheduled(session, day, day, "check"))["items"][0]["range"]
        assert period["done"] == 1 and period["remaining"] == 1


async def test_rule_changes_preserve_old_facts_and_revision_boundaries(sessions):
    """SCH-COMPILE-004 — tests/brd/schedules.feature"""
    async with sessions() as session:
        first = await create_card(session, kind="action", title="Water", schedule="twice a day", schedule_rule={"kind": "quota", "period": "day", "count": 2})
        old_revision, old_period = first.schedule_id, first.period_start
        result = await finish_action(session, first.id)
        current = await session.get(Card, result.successor_ids[0])
        await set_schedule(
            session, current, "three times a day", {"kind": "quota", "period": "day", "count": 3}
        )
        day = old_period.astimezone(ZoneInfo("Europe/Istanbul")).date()
        report = await get_scheduled(session, day, day, "card")
        assert len(report["items"]) == 1
        assert report["items"][0]["range"]["planned"] == 5
        assert report["items"][0]["range"]["done"] == 1
        assert first.schedule_id == old_revision
        assert first.schedule_record.valid_until is not None
        assert report["items"][0]["id"] == current.id
        assert report["items"][0]["next"]["remaining"] == 3


async def test_weekly_quota_chooses_sprint_and_outside_dates_choose_backlog(sessions):
    """SCH-STAGE-013 — tests/brd/schedules.feature"""
    from safwa.features.planning.use_cases import start_sprint
    from safwa.features.schedules.api import scheduled_stage

    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Exercise", stage="sprint", schedule="once a week", schedule_rule={"kind": "quota", "period": "week", "count": 1}
        )
        await start_sprint(session, success_criteria="Exercise")
        assert await scheduled_stage(session, card, card.period_start, "sprint") == "sprint"
        assert (
            await scheduled_stage(session, card, card.period_start + timedelta(days=90), "sprint")
            == "backlog"
        )


async def test_in_planning_a_planned_copy_due_today_opens_in_today(sessions):
    """SCH-STAGE-013 — tests/brd/schedules.feature"""
    from safwa.features.schedules.api import scheduled_stage

    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Water", stage="today", schedule="twice a day", schedule_rule={"kind": "quota", "period": "day", "count": 2}
        )
        tomorrow = card.period_start + timedelta(days=1)
        assert await scheduled_stage(session, card, card.period_start, "sprint") == "today"
        assert await scheduled_stage(session, card, tomorrow, "today") == "sprint"
        assert await scheduled_stage(session, card, card.period_start, "backlog") == "backlog"


async def test_read_tool_excludes_appointments_outside_the_range_and_writes_nothing(sessions):
    """SCH-WINDOW-002 — tests/brd/schedules.feature"""
    import json

    from llm_gateway import ToolCall
    from safwa.features.reminders.api import resolve, schedule_payload
    from safwa.features.schedules.agent import scheduled_tool
    from safwa.foundation.workspace import require_workspace

    async with sessions() as session:
        timing = resolve(
            day="31.12.2099", clock="10:00", now=datetime.now(UTC), tz=ZoneInfo("Europe/Istanbul")
        )
        card = await create_card(
            session,
            kind="action",
            title="Passport",
            schedule="31.12.2099 10:00",
            schedule_rule={"kind": "fixed", "timing": schedule_payload(timing)},
        )
        day = card.schedule_record.submitted_at.astimezone(ZoneInfo("Europe/Istanbul")).date()
        revision = (await require_workspace(session)).revision
        await session.commit()
    result = await scheduled_tool(sessions).run(
        ToolCall(
            id="read",
            name="get_scheduled",
            arguments_json=json.dumps(
                {"start_date": day.isoformat(), "end_date": day.isoformat(), "type": "card"}
            ),
        )
    )
    assert result["items"] == []
    async with sessions() as session:
        assert (await require_workspace(session)).revision == revision


async def test_an_answered_one_time_check_keeps_its_schedule_and_fact(sessions):
    """SCH-COMPILE-004 — tests/brd/schedules.feature"""
    from schedule_helpers import create_check as scheduled_check

    async with sessions() as session:
        check = await scheduled_check(session, title="Audit passed?", schedule="31.12.2099 10:00")
        period, revision = check.period_start, check.schedule_id
        _, successor = await resolve_check(session, check.id, "missed")
        assert successor is None
        with pytest.raises(DomainError, match="Pending"):
            await set_schedule(session, check, "weekly", {"kind": "quota", "period": "week", "count": 1})
        assert (check.schedule_id, check.period_start, check.outcome) == (
            revision,
            period,
            "missed",
        )


async def test_completing_a_future_quota_instance_counts_in_the_actual_day(sessions):
    """SCH-QUOTA-001 — tests/brd/schedules.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Exercise?", schedule="once a day", schedule_rule={"kind": "quota", "period": "day", "count": 1})
        today = check.period_start
        _, tomorrow = await resolve_check(session, check.id, "passed")
        assert tomorrow.period_start > today
        _, successor = await resolve_check(session, tomorrow.id, "passed")
        assert tomorrow.period_start == today and successor.period_start > today
        day = today.astimezone(ZoneInfo("Europe/Istanbul")).date()
        report = await get_scheduled(session, day, day + timedelta(days=1), "check")
        assert report["items"][0]["range"]["done"] == 2
        assert report["items"][0]["next"]["remaining"] == 1
        tomorrow_report = await get_scheduled(
            session, day + timedelta(days=1), day + timedelta(days=1), "check"
        )
        assert tomorrow_report["items"][0]["range"]["done"] == 0


async def test_appointments_are_counted_only_while_the_revision_is_active(sessions, monkeypatch):
    """SCH-WINDOW-002 — tests/brd/schedules.feature"""
    from safwa.features.reminders.api import resolve, schedule_payload

    submitted = datetime(2026, 10, 3, 10, tzinfo=UTC)
    monkeypatch.setattr("safwa.features.schedules.api.utcnow", lambda: submitted)
    async with sessions() as session:
        timing = resolve(
            days=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            clock="09:00",
            now=submitted,
            tz=ZoneInfo("Europe/Istanbul"),
        )
        check = await create_check(
            session,
            title="Exercise?",
            schedule="daily at 09:00",
            schedule_rule={"kind": "fixed", "timing": schedule_payload(timing)},
        )
        day = submitted.date()
        report = await get_scheduled(session, day, day + timedelta(days=1), "check")
        assert report["items"][0]["range"]["planned"] == 1
        monkeypatch.setattr(
            "safwa.features.schedules.api.utcnow", lambda: submitted + timedelta(hours=1)
        )
        await set_schedule(session, check, None, None)
        report = await get_scheduled(session, day, day + timedelta(days=1), "check")
        assert report["items"] == []


@pytest.mark.parametrize("entity_type", ["card", "check"])
async def test_deleting_last_open_instance_ends_the_plan_but_keeps_history(sessions, entity_type):
    """SCH-DELETE-007 — tests/brd/schedules.feature"""
    async with sessions() as session:
        rule = {"kind": "quota", "period": "day", "count": 1}
        first = await (
            create_card(session, kind="action", title="Run", schedule="daily", schedule_rule=rule)
            if entity_type == "card"
            else create_check(session, title="Posture?", schedule="daily", schedule_rule=rule)
        )
        day = first.period_start.astimezone(ZoneInfo("Europe/Istanbul")).date()
        if entity_type == "card":
            current_id = (await finish_action(session, first.id)).successor_ids[0]
            await delete_one_card(session, current_id)
        else:
            _, current = await resolve_check(session, first.id, "passed")
            await delete_check(session, current.id)
        await session.commit()
    async with sessions() as session:
        assert (
            await get_scheduled(
                session, day + timedelta(days=1), day + timedelta(days=1), entity_type
            )
        )["items"] == []
        history = (await get_scheduled(session, day, day, entity_type))["items"][0]
        assert history["range"]["done"] == 1
        assert history["next"] is None and history["total"]["remaining"] == 0


@pytest.mark.parametrize("last_schedule", [None, "31.12.2099 10:00"])
async def test_ended_tail_can_reopen_without_reopening_its_past_instances(sessions, last_schedule):
    """SCH-END-008 — tests/brd/schedules.feature"""
    from schedule_helpers import rule_for

    async with sessions() as session:
        first = await create_card(
            session,
            kind="action",
            title="Run",
            schedule="after completion",
            schedule_rule={"kind": "after_completion"},
        )
        current = await session.get(Card, (await finish_action(session, first.id)).successor_ids[0])
        await set_schedule(session, current, last_schedule, await rule_for(session, last_schedule))
        assert not (await finish_action(session, current.id)).successor_ids
        await move_card(session, current.id, CardStage.BACKLOG)
        with pytest.raises(DomainError, match="cannot be reopened"):
            await move_card(session, first.id, CardStage.BACKLOG)


def test_early_interval_completion_advances_beyond_the_finished_slot():
    """SCH-INTERVAL-009 — tests/brd/schedules.feature"""
    from safwa.features.reminders.api import resolve, schedule_payload

    now = datetime(2026, 10, 3, 9, tzinfo=UTC)
    timing = resolve(
        day="03.10.2026",
        clock="13:00",
        interval_minutes=60,
        now=now,
        tz=ZoneInfo("Europe/Istanbul"),
    )
    previous = now + timedelta(hours=1)
    repeats, at = next_slot(
        {"kind": "fixed", "timing": schedule_payload(timing)},
        previous,
        now,
        1,
        ZoneInfo("Europe/Istanbul"),
    )
    assert repeats and at == previous + timedelta(hours=1)


async def test_compiler_repairs_clock_arguments_without_asking_the_owner(sessions):
    """SCH-REPAIR-010 — tests/brd/schedules.feature"""
    calls = []

    async def complete(request):
        calls.append(request)
        return CompletionTurn(
            content="",
            tool_calls=[
                ToolCall(
                    id=str(len(calls)),
                    name="set_schedule_config",
                    arguments_json=json.dumps(
                        {"days": ["Mon"], "time": "9am" if len(calls) == 1 else "09:00"}
                    ),
                )
            ],
        )

    rule, question = await ScheduleCompiler(SimpleNamespace(complete=complete)).compile(
        "every Monday at 09:00", datetime.now(UTC), ZoneInfo("Europe/Istanbul"), "check"
    )
    assert len(calls) == 2
    assert json.loads(calls[1].messages[-1]["content"])["retryable"] is True
    assert question is None and rule["timing"]["weekdays"] == ["Mon"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"start_date": "03.10.2026", "end_date": "03.10.2026", "type": "check"},
        {"start_date": "2026-10-03", "end_date": "2026-10-01", "type": "check"},
        {"start_date": "2026-10-03", "end_date": "2027-10-01", "type": "check"},
        {"start_date": "2026-10-03", "end_date": "2026-10-03", "type": "other"},
        {},
    ],
)
async def test_read_argument_errors_are_repairable(sessions, arguments):
    """SCH-READ-011 — tests/brd/schedules.feature"""
    result = await scheduled_tool(sessions).run(
        ToolCall(id="read", name="get_scheduled", arguments_json=json.dumps(arguments))
    )
    assert result["status"] == "error" and result["retryable"] is True


async def test_one_compact_item_per_series_with_range_sprint_and_lifetime_counts(
    sessions, monkeypatch
):
    """SCH-SUMMARY-012 — tests/brd/schedules.feature"""
    from safwa.features.planning.use_cases import start_sprint

    submitted = datetime(2026, 10, 1, 6, tzinfo=UTC)
    monkeypatch.setattr("safwa.features.schedules.api.utcnow", lambda: submitted)
    monkeypatch.setattr("safwa.features.checks.use_cases.utcnow", lambda: submitted)
    async with sessions() as session:
        check = await create_check(session, title="Posture?", schedule="twice a day", schedule_rule={"kind": "quota", "period": "day", "count": 2})
        _, current = await resolve_check(session, check.id, "passed")
        today = datetime(2026, 10, 3, 6, tzinfo=UTC)
        monkeypatch.setattr("safwa.features.schedules.api.utcnow", lambda: today)
        monkeypatch.setattr("safwa.features.checks.use_cases.utcnow", lambda: today)
        monkeypatch.setattr("safwa.features.planning.use_cases.utcnow", lambda: today)
        await create_card(session, kind="action", title="Sprint scope", stage="sprint")
        sprint = await start_sprint(session, success_criteria="Care", length_days=14)
        _, current = await resolve_check(session, current.id, "missed")
        item = (await get_scheduled(session, today.date(), today.date(), "check"))["items"][0]
        assert item["id"] == current.id and item["series_id"] == check.id
        assert item["range"]["done"] == 1 and item["range"]["planned"] == 2
        assert item["sprint"]["planned"] == 28 and item["sprint"]["done"] == 1
        assert item["sprint"]["remaining"] == 27
        assert item["total"] == {"done": 2, "remaining": None, "passed": 1, "missed": 1}
        assert item["next"]["remaining"] == 1
        assert sprint.planned_start_date == today.date()


async def test_result_is_bounded_and_continuation_loses_no_series(sessions):
    """SCH-SUMMARY-012 — tests/brd/schedules.feature"""
    async with sessions() as session:
        for i in range(35):
            check = await create_check(session, title=f"Routine {i}?", schedule="once a day", schedule_rule={"kind": "quota", "period": "day", "count": 1})
        day = check.period_start.astimezone(ZoneInfo("Europe/Istanbul")).date()
        first = await get_scheduled(session, day, day + timedelta(days=92), "check")
        assert len(json.dumps(first, ensure_ascii=False)) <= DEFAULT_CHAR_BUDGET
        assert "periods" not in json.dumps(first)
        ids = [item["series_id"] for item in first["items"]]
        page = first
        while "next_after_id" in page:
            page = await get_scheduled(
                session, day, day + timedelta(days=92), "check", after_id=page["next_after_id"]
            )
            assert len(json.dumps(page, ensure_ascii=False)) <= DEFAULT_CHAR_BUDGET
            ids.extend(item["series_id"] for item in page["items"])
        assert len(ids) == len(set(ids)) == 35


async def test_long_escaped_text_cannot_overflow_the_first_summary_item(sessions):
    """SCH-SUMMARY-012 — tests/brd/schedules.feature"""
    async with sessions() as session:
        check = await create_check(
            session,
            title='"' * 3000,
            schedule='"' * 3000,
            schedule_rule={"kind": "after_completion"},
        )
        day = check.schedule_record.submitted_at.astimezone(ZoneInfo("Europe/Istanbul")).date()
        report = await get_scheduled(session, day, day, "check")
        assert len(json.dumps(report, ensure_ascii=False)) <= DEFAULT_CHAR_BUDGET
        assert report["items"][0]["text_truncated"] is True
        assert check.schedule == '"' * 3000


@pytest.mark.parametrize("entity_type", ["card", "check"])
async def test_one_time_plan_reports_its_appointment_and_finite_remainder(sessions, entity_type):
    """SCH-SUMMARY-012 — tests/brd/schedules.feature"""
    from datetime import date

    from schedule_helpers import create_card as scheduled_card
    from schedule_helpers import create_check as scheduled_check

    async with sessions() as session:
        entity = await (
            scheduled_card(session, kind="action", title="Audit", schedule="31.12.2099 10:00")
            if entity_type == "card"
            else scheduled_check(session, title="Audit passed?", schedule="31.12.2099 10:00")
        )
        day = date(2099, 12, 31)
        before = (await get_scheduled(session, day, day, entity_type))["items"][0]
        assert before["next"] == {"at": "2099-12-31T10:00:00+03:00"}
        assert before["total"]["remaining"] == 1
        if entity_type == "card":
            assert not (await finish_action(session, entity.id)).successor_ids
        else:
            assert (await resolve_check(session, entity.id, "passed"))[1] is None
        after = (await get_scheduled(session, day, day, entity_type))["items"][0]
        assert after["range"]["done"] == 1 and after["range"]["remaining"] == 0
        assert after["total"]["done"] == 1 and after["total"]["remaining"] == 0
        assert after["next"] is None


@pytest.mark.parametrize("entity_type", ["card", "check"])
async def test_undated_repetition_reports_actual_progress_without_inventing_a_plan(
    sessions, entity_type
):
    """SCH-SUMMARY-012 — tests/brd/schedules.feature"""
    async with sessions() as session:
        rule = {"kind": "after_completion"}
        entity = await (
            create_card(
                session,
                kind="action",
                title="Practice",
                schedule="after completion",
                schedule_rule=rule,
            )
            if entity_type == "card"
            else create_check(
                session, title="Practised?", schedule="after completion", schedule_rule=rule
            )
        )
        day = entity.schedule_record.submitted_at.astimezone(ZoneInfo("Europe/Istanbul")).date()
        if entity_type == "card":
            await finish_action(session, entity.id)
        else:
            await resolve_check(session, entity.id, "missed")
        item = (await get_scheduled(session, day, day, entity_type))["items"][0]
        assert item["range"]["planned"] is None and item["range"]["remaining"] is None
        assert item["range"]["done"] == item["total"]["done"] == 1
        assert item["total"]["remaining"] is None
        assert item["next"] == {"after_completion": True}
        assert item["sprint"] is None


def _answering(name, arguments):
    """A provider that ends the compiler's session with one terminal call."""
    requests = []

    async def complete(request):
        requests.append(request)
        return CompletionTurn(
            content="",
            tool_calls=[ToolCall(id="1", name=name, arguments_json=json.dumps(arguments))],
        )

    return SimpleNamespace(complete=complete), requests


@pytest.mark.parametrize(
    ("config", "limited"),
    [
        ({"period": "day", "count": ACTION_DAILY_EXECUTIONS_MAX}, False),
        ({"period": "day", "count": ACTION_DAILY_EXECUTIONS_MAX + 1}, True),
        ({"period": "week", "count": 7 * ACTION_DAILY_EXECUTIONS_MAX + 1}, True),
        ({"interval_minutes": 24 * 60 // ACTION_DAILY_EXECUTIONS_MAX}, False),
        ({"interval_minutes": 24 * 60 // ACTION_DAILY_EXECUTIONS_MAX - 1}, True),
    ],
)
async def test_an_action_repeats_at_most_ten_times_a_day(config, limited):
    """SCH-LIMIT-015 — tests/brd/schedules.feature"""
    now, tz = datetime.now(UTC), ZoneInfo("Europe/Istanbul")
    provider, _ = _answering("set_schedule_config", config)
    rule, question = await ScheduleCompiler(provider).compile("often", now, tz, "action")
    assert (rule is None, question) == ((True, ACTION_LIMIT_QUESTION) if limited else (False, None))
    rule, question = await ScheduleCompiler(provider).compile("often", now, tz, "check")
    assert rule is not None and question is None


async def test_a_goal_deadline_is_one_date_that_plans_and_blocks_nothing(sessions):
    """CD-DEADLINE-043 — tests/brd/cards.feature"""
    from safwa.features.cards.api import list_order
    from safwa.features.cards.use_cases import finish_card

    tz = ZoneInfo("Europe/Istanbul")
    provider, requests = _answering("set_deadline", {"date": "20.10.2099"})
    rule, question = await ScheduleCompiler(provider).compile(
        "by 20 October 2099", datetime.now(UTC), tz, "deadline"
    )
    assert (rule, question) == ({"kind": "deadline", "date": "2099-10-20", "time": None}, None)
    assert "Read one Deadline" in requests[0].messages[0]["content"]
    async with sessions() as session:
        goal = await create_card(
            session, kind="goal", title="Ship v2", schedule="by 20 October 2099", schedule_rule=rule
        )
        assert goal.deadline_at == datetime(2099, 10, 20, 23, 59, tzinfo=tz)
        assert goal.scheduled_at is None
        later = await create_card(session, kind="goal", title="Later")
        assert sorted([later, goal], key=list_order) == [goal, later]
        day = goal.deadline_at.astimezone(tz).date()
        assert (await get_scheduled(session, day, day, "card"))["items"] == []
        await finish_card(session, goal.id)
        assert goal.completed_at is not None


@pytest.mark.parametrize("entity", ["card", "check"])
async def test_a_proposed_schedule_is_read_before_its_proposal_is_saved(sessions, entity):
    """SCH-COMPILE-004 — tests/brd/schedules.feature"""
    from safwa.bootstrap.modules import PROPOSALS
    from safwa.features.cards.telegram.review import CardProposalPresenter
    from safwa.features.checks.telegram.review import CheckProposalPresenter
    from tg_agent_shell.proposals.api import ApplyContext, ProposalChange
    from tg_agent_shell.proposals.prepare import ChangePreparer

    provider, requests = _answering("set_schedule_config", {"period": "day", "count": 1})
    arguments = (
        {"mode": "create", "kind": "action", "title": "Walk", "schedule": "every evening"}
        if entity == "card"
        else {"mode": "create", "title": "Walked?", "schedule": "every evening"}
    )
    async with sessions() as session:
        change = PROPOSALS.change_from_tool(entity, arguments)
        prepared = await ChangePreparer(provider, None, PROPOSALS).prepare(session, change)
        assert len(requests) == 1
        assert prepared.values["schedule_rule"] == {"kind": "quota", "period": "day", "count": 1}
        proposal = ProposalChange(entity=entity, action=change.action, values=prepared.values)
        presenter = CardProposalPresenter() if entity == "card" else CheckProposalPresenter()
        screen = await presenter.screen(session, [proposal])
        assert "every evening" in screen.blocks[0] and "1 per day." in screen.blocks[0]
        [created] = await PROPOSALS.handler(entity).apply(ApplyContext(session, frozenset()), proposal)
        if entity == "card":
            assert (await finish_action(session, created)).successor_ids
        else:
            assert (await resolve_check(session, created, "passed"))[1] is not None


async def test_an_unclear_proposed_schedule_is_asked_in_the_same_reply(sessions):
    """SCH-CLARIFY-005 — tests/brd/schedules.feature"""
    from sqlalchemy import func, select

    from safwa.bootstrap.modules import HOOKS, PROPOSALS
    from tg_agent_shell.proposals.api import ToolPreparationError
    from tg_agent_shell.proposals.prepare import ChangePreparer

    provider, _ = _answering("not_clear_enough", {"reason": "When does it happen?"})
    change = PROPOSALS.change_from_tool(
        "card", {"mode": "create", "kind": "action", "title": "Walk", "schedule": "sometimes"}
    )
    async with sessions() as session:
        with pytest.raises(ToolPreparationError) as refusal:
            await ChangePreparer(provider, None, PROPOSALS).prepare(session, change)
        result = refusal.value.as_tool_result()
        assert (result["code"], result["error"]) == ("schedule_unclear", "When does it happen?")
        assert result["hint"].startswith("Ask the user this exact question")
        assert await session.scalar(select(func.count(Card.id))) == 0
    assert not [hook.name for hook in HOOKS if hook.owner == "schedules"]


async def test_a_schedule_the_model_cannot_read_asks_for_other_words(sessions):
    """SCH-RETRY-014 — tests/brd/schedules.feature"""
    from sqlalchemy import func, select

    from safwa.bootstrap.modules import PROPOSALS
    from safwa.features.checks.model import Check
    from tg_agent_shell.proposals.api import ToolPreparationError
    from tg_agent_shell.proposals.prepare import ChangePreparer

    async def complete(request):
        return CompletionTurn(content="Probably in the evening.", tool_calls=[])

    provider = SimpleNamespace(complete=complete)
    change = PROPOSALS.change_from_tool(
        "check", {"mode": "create", "title": "Walked?", "schedule": "whenever it fits"}
    )
    async with sessions() as session:
        with pytest.raises(ToolPreparationError) as refusal:
            await ChangePreparer(provider, None, PROPOSALS).prepare(session, change)
        assert str(refusal.value) == UNREADABLE_QUESTION.format(label="Schedule")
        assert await session.scalar(select(func.count(Check.id))) == 0


async def test_a_clock_alone_never_makes_a_one_time_appointment():
    """SCH-CLOCK-017 — tests/brd/schedules.feature"""
    from safwa.features.schedules.api import rule_summary

    every_day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    calls = []

    async def complete(request):
        calls.append(request)
        arguments = {"time": "20:00"} if len(calls) == 1 else {"days": every_day, "time": "20:00"}
        return CompletionTurn(
            content="",
            tool_calls=[
                ToolCall(
                    id=str(len(calls)),
                    name="set_schedule_config",
                    arguments_json=json.dumps(arguments),
                )
            ],
        )

    tz = ZoneInfo("Europe/Istanbul")
    rule, question = await ScheduleCompiler(SimpleNamespace(complete=complete)).compile(
        "every evening at 20:00", datetime.now(UTC), tz, "action"
    )
    repair = json.loads(calls[1].messages[-1]["content"])
    assert repair["retryable"] is True and "A time alone" in json.dumps(repair)
    assert question is None and rule["timing"]["schedule_kind"] == "daily"
    assert rule_summary(rule, tz) == "Every day at 20:00."
    monday = datetime(2026, 10, 5, 10, tzinfo=tz).astimezone(UTC)
    provider, _ = _answering("set_schedule_config", {"date": "07.10.2026", "time": "15:00"})
    once, question = await ScheduleCompiler(provider).compile(
        "this Wednesday at 15:00", monday, tz, "action"
    )
    assert question is None and once["timing"]["schedule_kind"] == "once"
    assert rule_summary(once, tz) == "Once, at 2026-10-07 15:00."


async def test_a_schedule_without_a_clock_is_an_appointment_for_its_whole_day(
    sessions, monkeypatch
):
    """SCH-DAY-018 — tests/brd/schedules.feature"""
    from datetime import date

    from safwa.features.schedules.api import appointment_label, rule_summary, schedule_summary
    from safwa.features.schedules.rules import END_OF_DAY

    assert "'every day', 'daily', 'every evening', 'every morning': period=day, count=1." in COMPILER_PROMPT
    assert "Morning, evening and night are not a time: leave time out." in COMPILER_PROMPT
    tz = ZoneInfo("Europe/Istanbul")
    monday = date(2026, 10, 5)
    noon = datetime(2026, 10, 5, 12, tzinfo=tz).astimezone(UTC)
    provider, _ = _answering("set_schedule_config", {"days": ["Mon"]})
    rule, question = await ScheduleCompiler(provider).compile("every Monday", noon, tz, "action")
    assert question is None and rule["all_day"] is True
    assert rule_summary(rule, tz) == "Every Mon, any time that day."
    provider, _ = _answering("set_schedule_config", {"date": "05.10.2026"})
    once, question = await ScheduleCompiler(provider).compile("today", noon, tz, "action")
    assert question is None and rule_summary(once, tz) == "On Mon 05.10.2026, any time that day."

    monkeypatch.setattr("safwa.features.schedules.api.utcnow", lambda: noon)
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Laundry", schedule="every Monday", schedule_rule=rule
        )
        assert card.scheduled_at == datetime.combine(monday, END_OF_DAY, tzinfo=tz)
        assert await schedule_summary(session, card) == (
            "Every Mon, any time that day. Appointment: Mon 05.10."
        )
        assert appointment_label(rule, card.scheduled_at, tz, "%d.%m") == "05.10"
        item = (await get_scheduled(session, monday, monday, "card"))["items"][0]
        assert item["range"]["planned"] == 1 and item["next"] == {"date": "2026-10-05"}
        monkeypatch.setattr(
            "safwa.features.schedules.api.utcnow", lambda: noon + timedelta(hours=11)
        )
        assert "overdue" not in await schedule_summary(session, card)
        monkeypatch.setattr("safwa.features.schedules.api.utcnow", lambda: noon + timedelta(days=1))
        assert (await schedule_summary(session, card)).endswith("It is overdue.")
