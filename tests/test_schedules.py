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
from safwa.features.schedules.agent import ScheduleCompiler, scheduled_tool
from safwa.features.schedules.api import assign_first, get_scheduled, set_schedule
from safwa.features.schedules.hooks import compile_revision, recover
from safwa.features.schedules.model import ScheduleDefinition
from safwa.features.schedules.rules import next_slot, period_start
from tg_agent_shell.ai.sql import DEFAULT_CHAR_BUDGET
from tg_agent_shell.foundation.errors import DomainError


async def ready(session, entity, rule):
    entity.schedule_record.status = "ready"
    entity.schedule_record.rule = rule
    await assign_first(session, entity)
    await session.flush()


async def test_daily_quota_opens_one_instance_and_counts_finished_facts(sessions):
    """SCH-QUOTA-001 — tests/brd/schedules.feature"""
    async with sessions() as session:
        first = await create_card(
            session, kind="action", title="Water", schedule="five times a day"
        )
        await ready(session, first, {"kind": "quota", "period": "day", "count": 5})
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


async def test_partial_week_keeps_the_whole_quota(sessions):
    """SCH-WINDOW-002 — tests/brd/schedules.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Stretch?", schedule="once a week")
        await ready(session, check, {"kind": "quota", "period": "week", "count": 1})
        day = check.schedule_record.submitted_at.astimezone(ZoneInfo("Europe/Istanbul")).date()
        result = await get_scheduled(session, day, day, "check")
        period = result["items"][0]["range"]
        assert period["planned"] == 1
        assert period["partial"] is True
        assert check.scheduled_at is None


async def test_missed_is_an_answer_and_scheduled_checks_stay_independent(sessions):
    """SCH-CHECK-003 — tests/brd/schedules.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Straight posture?", schedule="twice a day")
        await ready(session, check, {"kind": "quota", "period": "day", "count": 2})
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


async def test_compiler_drops_stale_results(sessions):
    """SCH-COMPILE-004 — tests/brd/schedules.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Run", schedule="daily")
        id, card_id = card.schedule_id, card.id
        await session.commit()

    async def compile(text, submitted_at, tz):
        async with sessions() as session:
            card = await session.get(Card, card_id)
            await set_schedule(session, card, "weekly")
            await session.commit()
        return {"kind": "quota", "period": "day", "count": 1}, None

    context = SimpleNamespace(
        sessions=sessions,
        resources=SimpleNamespace(schedule_compiler=SimpleNamespace(compile=compile)),
    )
    await compile_revision(id, context)
    async with sessions() as session:
        card = await session.get(Card, card_id)
        assert card.schedule_record.status == "pending"
        old = await session.get(ScheduleDefinition, id)
        assert old.rule is None and old.valid_until is not None


async def test_ambiguous_revision_is_asked_once_and_not_reparsed_hourly(sessions):
    """SCH-CLARIFY-005 — tests/brd/schedules.feature"""
    calls = []

    async def compile(text, submitted_at, tz):
        calls.append(text)
        return None, "How many times per week?"

    context = SimpleNamespace(
        sessions=sessions,
        resources=SimpleNamespace(schedule_compiler=SimpleNamespace(compile=compile)),
    )
    async with sessions() as session:
        check = await create_check(session, title="Exercise?", schedule="often")
        id = check.schedule_id
        await session.commit()
    await compile_revision(id, context)
    await recover(None, context)
    assert calls == ["often"]
    async with sessions() as session:
        definition = await session.get(ScheduleDefinition, id)
        assert definition.status == "needs_clarification"


def test_local_calendar_week_and_dst_do_not_use_utc_days():
    """SCH-ZONE-006 — tests/brd/schedules.feature"""
    at = datetime(2026, 3, 29, 23, 30, tzinfo=UTC)
    assert period_start(at, "week", ZoneInfo("Europe/Berlin")) == datetime(
        2026, 3, 29, 22, tzinfo=UTC
    )


async def test_pending_schedule_refuses_completion_before_writing_the_fact(sessions):
    """SCH-COMPILE-004 — tests/brd/schedules.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Run", schedule="often")
        with pytest.raises(DomainError, match="configured"):
            await finish_action(session, card.id)
        assert card.effective_stage == "backlog" and card.completed_at is None
        await set_schedule(session, card, None)
        assert (await finish_action(session, card.id)).successor_ids == []


async def test_late_quota_answer_belongs_to_today_without_carrying_yesterdays_quota(sessions):
    """SCH-QUOTA-001 — tests/brd/schedules.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Stretch?", schedule="twice a day")
        await ready(session, check, {"kind": "quota", "period": "day", "count": 2})
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
        first = await create_card(session, kind="action", title="Water", schedule="twice a day")
        await ready(session, first, {"kind": "quota", "period": "day", "count": 2})
        old_revision, old_period = first.schedule_id, first.period_start
        result = await finish_action(session, first.id)
        current = await session.get(Card, result.successor_ids[0])
        await set_schedule(session, current, "three times a day")
        await ready(session, current, {"kind": "quota", "period": "day", "count": 3})
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
    """SCH-WINDOW-002 — tests/brd/schedules.feature"""
    from safwa.features.planning.use_cases import start_sprint
    from safwa.features.schedules.api import scheduled_stage

    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Exercise", stage="sprint", schedule="once a week"
        )
        await ready(session, card, {"kind": "quota", "period": "week", "count": 1})
        await start_sprint(session, success_criteria="Exercise")
        assert await scheduled_stage(session, card, card.period_start) == "sprint"
        assert (
            await scheduled_stage(session, card, card.period_start + timedelta(days=90))
            == "backlog"
        )


async def test_read_tool_excludes_appointments_outside_the_range_and_writes_nothing(sessions):
    """SCH-WINDOW-002 — tests/brd/schedules.feature"""
    import json

    from llm_gateway import ToolCall
    from safwa.features.reminders.api import resolve, schedule_payload
    from safwa.features.schedules.agent import scheduled_tool
    from safwa.foundation.workspace import require_workspace

    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Passport", schedule="31.12.2099 10:00"
        )
        timing = resolve(
            day="31.12.2099",
            clock="10:00",
            now=card.schedule_record.submitted_at,
            tz=ZoneInfo("Europe/Istanbul"),
        )
        await ready(session, card, {"kind": "fixed", "timing": schedule_payload(timing)})
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


async def test_recovery_creates_missing_definition_and_retries_provider_failure(sessions):
    """SCH-CLARIFY-005 — tests/brd/schedules.feature"""
    calls = []

    async def compile(text, submitted_at, tz):
        calls.append(submitted_at)
        if len(calls) == 1:
            raise TimeoutError("provider unavailable")
        return {"kind": "quota", "period": "week", "count": 1}, None

    context = SimpleNamespace(
        sessions=sessions,
        resources=SimpleNamespace(schedule_compiler=SimpleNamespace(compile=compile)),
    )
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Review")
        card.schedule = "once a week"
        await session.commit()
        card_id = card.id
    await recover(None, context)
    await recover(None, context)
    assert len(calls) == 2 and calls[0] == calls[1]
    async with sessions() as session:
        card = await session.get(Card, card_id)
        assert card.schedule_record.status == "ready"


async def test_an_answered_one_time_check_keeps_its_schedule_and_fact(sessions):
    """SCH-COMPILE-004 — tests/brd/schedules.feature"""
    from schedule_helpers import create_check as scheduled_check

    async with sessions() as session:
        check = await scheduled_check(session, title="Audit passed?", schedule="31.12.2099 10:00")
        period, revision = check.period_start, check.schedule_id
        _, successor = await resolve_check(session, check.id, "missed")
        assert successor is None
        with pytest.raises(DomainError, match="Pending"):
            await set_schedule(session, check, "weekly")
        assert (check.schedule_id, check.period_start, check.outcome) == (
            revision,
            period,
            "missed",
        )


async def test_completing_a_future_quota_instance_counts_in_the_actual_day(sessions):
    """SCH-QUOTA-001 — tests/brd/schedules.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Exercise?", schedule="once a day")
        await ready(session, check, {"kind": "quota", "period": "day", "count": 1})
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
        check = await create_check(session, title="Exercise?", schedule="daily at 09:00")
        timing = resolve(
            days=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            clock="09:00",
            now=submitted,
            tz=ZoneInfo("Europe/Istanbul"),
        )
        await ready(session, check, {"kind": "fixed", "timing": schedule_payload(timing)})
        day = submitted.date()
        report = await get_scheduled(session, day, day + timedelta(days=1), "check")
        assert report["items"][0]["range"]["planned"] == 1
        monkeypatch.setattr(
            "safwa.features.schedules.api.utcnow", lambda: submitted + timedelta(hours=1)
        )
        await set_schedule(session, check, None)
        report = await get_scheduled(session, day, day + timedelta(days=1), "check")
        assert report["items"] == []


@pytest.mark.parametrize("entity_type", ["card", "check"])
async def test_deleting_last_open_instance_ends_the_plan_but_keeps_history(sessions, entity_type):
    """SCH-DELETE-007 — tests/brd/schedules.feature"""
    async with sessions() as session:
        first = await (
            create_card(session, kind="action", title="Run", schedule="daily")
            if entity_type == "card"
            else create_check(session, title="Posture?", schedule="daily")
        )
        await ready(session, first, {"kind": "quota", "period": "day", "count": 1})
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
    from schedule_helpers import configure

    async with sessions() as session:
        first = await create_card(session, kind="action", title="Run", schedule="after completion")
        await ready(session, first, {"kind": "after_completion"})
        current = await session.get(Card, (await finish_action(session, first.id)).successor_ids[0])
        await set_schedule(session, current, last_schedule)
        await configure(session, current)
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

    async with sessions() as session:
        check = await create_check(session, title="Workout?", schedule="every Monday at 09:00")
        id, check_id = check.schedule_id, check.id
        await session.commit()
    await compile_revision(
        id,
        SimpleNamespace(
            sessions=sessions,
            resources=SimpleNamespace(
                schedule_compiler=ScheduleCompiler(SimpleNamespace(complete=complete))
            ),
        ),
    )
    assert len(calls) == 2
    assert json.loads(calls[1].messages[-1]["content"])["retryable"] is True
    async with sessions() as session:
        check = await session.get(type(check), check_id)
        assert check.schedule_record.status == "ready" and check.schedule_record.question is None


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
        check = await create_check(session, title="Posture?", schedule="twice a day")
        await ready(session, check, {"kind": "quota", "period": "day", "count": 2})
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
            check = await create_check(session, title=f"Routine {i}?", schedule="once a day")
            await ready(session, check, {"kind": "quota", "period": "day", "count": 1})
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
        check = await create_check(session, title='"' * 3000, schedule='"' * 3000)
        check.schedule_record.status = "needs_clarification"
        check.schedule_record.question = '"' * 3000
        day = check.schedule_record.submitted_at.astimezone(ZoneInfo("Europe/Istanbul")).date()
        report = await get_scheduled(session, day, day, "check")
        assert len(json.dumps(report, ensure_ascii=False)) <= DEFAULT_CHAR_BUDGET
        assert report["items"][0]["text_truncated"] is True
        assert check.schedule == '"' * 3000


@pytest.mark.parametrize("entity_type", ["card", "check"])
async def test_one_time_plan_reports_its_appointment_and_finite_remainder(sessions, entity_type):
    """SCH-SUMMARY-012 — tests/brd/schedules.feature"""
    from datetime import date

    from schedule_helpers import configure

    async with sessions() as session:
        entity = await (
            create_card(session, kind="action", title="Audit", schedule="31.12.2099 10:00")
            if entity_type == "card"
            else create_check(session, title="Audit passed?", schedule="31.12.2099 10:00")
        )
        await configure(session, entity)
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
        entity = await (
            create_card(session, kind="action", title="Practice", schedule="after completion")
            if entity_type == "card"
            else create_check(session, title="Practised?", schedule="after completion")
        )
        await ready(session, entity, {"kind": "after_completion"})
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
