"""The shared boundary for a source edit, an occurrence and a read-only plan."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import DEFAULT_CELL_LIMIT, DEFAULT_CHAR_BUDGET, DEFAULT_ROW_LIMIT
from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError

from ...foundation.workspace import require_workspace
from ..cards.model import Card
from ..checks.model import Check
from .model import ScheduleDefinition
from .rules import first_slot, next_slot, period_end, period_start, windows

SCHEDULE_CHANGED = "schedule.changed"
SCHEDULE_UNCLEAR = "schedule.unclear"
SCHEDULE_INSTRUCTION = "Describe the schedule, e.g. once a week, five times a day, or Tuesday at 15:00. Send off to clear it."


async def workspace_zone(session: AsyncSession) -> ZoneInfo:
    return ZoneInfo((await require_workspace(session)).timezone)


async def set_schedule(session: AsyncSession, entity: Card | Check, text: str | None) -> None:
    text = (text or "").strip() or None
    if text and text.casefold() == "off":
        text = None
    if entity.schedule == text:
        return
    if entity.is_closed_repeat():
        raise DomainError("Edit Schedule on the current instance of this series")
    if (isinstance(entity, Card) and entity.completed_at is not None) or (
        isinstance(entity, Check) and entity.outcome is not None
    ):
        raise DomainError(
            "Edit Schedule on an open Action or Pending Check to preserve the completed instance"
        )
    type_ = "card" if isinstance(entity, Card) else "check"
    series_id = (entity.repeat_series_id if type_ == "card" else entity.series_id) or entity.id
    now = utcnow()
    old = await session.scalar(
        select(ScheduleDefinition).where(
            ScheduleDefinition.entity_id == series_id,
            ScheduleDefinition.type == type_,
            ScheduleDefinition.valid_until.is_(None),
        )
    )
    if old:
        old.valid_until = now
    definition = ScheduleDefinition(
        entity_id=series_id,
        type=type_,
        source_text=text,
        submitted_at=now,
        status="pending" if text else "disabled",
    )
    session.add(definition)
    await session.flush()
    entity.schedule = text
    entity.schedule_record = definition
    entity.period_start = None
    record_change(session, SCHEDULE_CHANGED, definition.id)


async def close_deleted_schedules(
    session: AsyncSession, model: type[Card] | type[Check], ids: list[int]
) -> None:
    """End the plan only when deletion removes its last open instance."""
    series = func.coalesce(model.repeat_series_id if model is Card else model.series_id, model.id)
    roots = set(await session.scalars(select(series).where(model.id.in_(ids))))
    open_ = model.completed_at.is_(None) if model is Card else model.outcome.is_(None)
    remaining = set(
        await session.scalars(select(series).where(series.in_(roots), model.id.notin_(ids), open_))
    )
    for definition in await session.scalars(
        select(ScheduleDefinition).where(
            ScheduleDefinition.type == ("card" if model is Card else "check"),
            ScheduleDefinition.entity_id.in_(roots - remaining),
            ScheduleDefinition.valid_until.is_(None),
        )
    ):
        definition.valid_until = utcnow()


async def occurrence_count(session: AsyncSession, entity: Card | Check) -> int:
    model = type(entity)
    answered = (
        Card.completed_at.is_not(None) if isinstance(entity, Card) else Check.outcome.is_not(None)
    )
    return int(
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(
                model.schedule_id == entity.schedule_id,
                model.period_start == entity.period_start,
                answered,
            )
        )
        or 0
    )


async def successor_slot(
    session: AsyncSession, entity: Card | Check
) -> tuple[bool, datetime | None]:
    definition = entity.schedule_record
    if definition is None or not definition.rule:
        return False, None
    await session.flush()
    return next_slot(
        definition.rule,
        entity.period_start,
        utcnow(),
        await occurrence_count(session, entity),
        await workspace_zone(session),
    )


async def prepare_occurrence(session: AsyncSession, entity: Card | Check) -> None:
    """Validate before changing a fact; unused quota does not carry into the next period."""
    definition = entity.schedule_record
    if entity.schedule and (definition is None or definition.status != "ready"):
        raise DomainError("Schedule needs to be configured before this instance can be finished")
    if definition and definition.rule and definition.rule["kind"] == "quota":
        entity.period_start = period_start(
            utcnow(), definition.rule["period"], await workspace_zone(session)
        )


async def assign_first(session: AsyncSession, entity: Card | Check) -> None:
    definition = entity.schedule_record
    if definition and definition.status == "ready" and definition.rule:
        entity.period_start = first_slot(
            definition.rule, definition.submitted_at, await workspace_zone(session)
        )


async def scheduled_stage(
    session: AsyncSession, card: Card, slot: datetime | None, previous_stage: str | None = None
) -> str:
    """Only generated Actions are placed automatically; a quota never invents a clock."""
    from ..planning.model import Sprint

    workspace = await require_workspace(session)
    end = await session.scalar(select(Sprint.planned_end_date).where(Sprint.id == workspace.active_sprint_id))
    if end is None:
        return "backlog"
    if slot is None:
        stage = previous_stage or card.manual_stage
        return stage if stage in {"today", "sprint"} else "sprint"
    tz = await workspace_zone(session)
    day = slot.astimezone(tz).date()
    today = utcnow().astimezone(tz).date()
    rule = card.schedule_record.rule
    if rule["kind"] == "quota" and rule["period"] == "week":
        return "sprint" if day <= end else "backlog"
    if day <= today:
        return "today"
    return "sprint" if day <= end else "backlog"


async def schedule_progress(session: AsyncSession, entity: Card | Check) -> str | None:
    definition = entity.schedule_record
    if not definition or not definition.rule or definition.rule["kind"] != "quota":
        return None
    label = "completed" if isinstance(entity, Card) else "answered"
    day = (
        entity.period_start.astimezone(await workspace_zone(session)).date()
        if entity.period_start
        else None
    )
    return f"{await occurrence_count(session, entity)}/{definition.rule['count']} {label} for {definition.rule['period']} starting {day}"


async def remaining_occurrences(
    session: AsyncSession, card: Card, start_date: date, end_date: date
) -> int | None:
    """Open work in the chosen calendar window, without allocating a weekly quota to days."""
    if not card.schedule:
        return 1
    definition = card.schedule_record
    if not definition or definition.status != "ready" or not definition.rule:
        return None
    rule = definition.rule
    if rule["kind"] == "after_completion":
        return None
    tz = await workspace_zone(session)
    start, end = _date_bounds(start_date, end_date, tz)
    floor = max(start, definition.submitted_at) if rule["kind"] == "fixed" else start
    periods = windows(rule, floor, end, tz)
    periods = [(at, until, count) for at, until, count in periods
               if until > definition.submitted_at]
    if not periods:
        return int(rule["kind"] == "fixed" and card.period_start is not None
                   and card.period_start < start)
    facts = list((await session.execute(
        select(Card.period_start, func.count()).where(
            Card.schedule_id == card.schedule_id,
            Card.completed_at.is_not(None),
            Card.period_start >= periods[0][0], Card.period_start < periods[-1][1],
        ).group_by(Card.period_start)
    )).all())
    count = sum(max(0, planned - sum(done for slot, done in facts if at <= slot < until))
                for at, until, planned in periods)
    if rule["kind"] == "fixed" and card.period_start is not None and card.period_start < start:
        count += 1
    return count


def _date_bounds(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time(), tzinfo=tz).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time(), tzinfo=tz).astimezone(UTC),
    )


def _summary(definitions, facts, start: datetime, end: datetime, tz: ZoneInfo, checks: bool):
    planned, unknown, partial = 0, False, False
    counted = set()
    for definition in definitions:
        if (
            not definition.source_text
            or definition.submitted_at >= end
            or (definition.valid_until and definition.valid_until <= start)
        ):
            continue
        rule = definition.rule
        if not rule or rule["kind"] == "after_completion":
            unknown = True
            continue
        floor, ceiling = start, end
        if rule["kind"] == "fixed":
            floor, ceiling = (
                max(start, definition.submitted_at),
                min(end, definition.valid_until or end),
            )
        for at, until, count in windows(rule, floor, ceiling, tz):
            if until <= definition.submitted_at or (
                definition.valid_until and at >= definition.valid_until
            ):
                continue
            planned += count
            partial |= not (
                start <= at
                and until <= end
                and definition.submitted_at <= at
                and (definition.valid_until is None or until <= definition.valid_until)
            )
            counted.update(
                f.id
                for f in facts
                if f.schedule_id == definition.id
                and f.period_start is not None
                and at <= f.period_start < until
            )
    # Undated repetition and ordinary completion after clearing Schedule use actual time.
    by_id = {d.id: d for d in definitions}
    counted.update(
        f.id
        for f in facts
        if f.period_start is None
        and start <= f.at < end
        and (f.schedule_id in by_id or f.schedule_id is None)
    )
    selected = [f for f in facts if f.id in counted]
    result = {
        "planned": None if unknown else planned,
        "done": len(selected),
        "remaining": None if unknown else max(0, planned - len(selected)),
    }
    if partial:
        result["partial"] = True
    if checks:
        result.update(
            passed=sum(f.outcome == "passed" for f in selected),
            missed=sum(f.outcome == "missed" for f in selected),
        )
    return result


async def get_scheduled(
    session: AsyncSession,
    start_date: date,
    end_date: date,
    type: str,
    *,
    after_id: int | None = None,
) -> dict[str, Any]:
    """One bounded summary per series; revisions retain the meaning of old facts."""
    if type not in {"card", "check"} or not 0 <= (end_date - start_date).days <= 92:
        raise DomainError("Use card or check and an ordered date range of at most 93 days")
    from ..planning.model import Sprint

    workspace = await require_workspace(session)
    tz, now = ZoneInfo(workspace.timezone), utcnow()
    start, end = _date_bounds(start_date, end_date, tz)
    sprint = (
        await session.get(Sprint, workspace.active_sprint_id)
        if workspace.active_sprint_id
        else None
    )
    sprint_bounds = (
        _date_bounds(sprint.planned_start_date, sprint.planned_end_date, tz) if sprint else None
    )
    result = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "timezone": tz.key,
        "as_of": now.isoformat(),
        "type": type,
        "sprint": {
            "id": sprint.id,
            "start_date": sprint.planned_start_date.isoformat(),
            "end_date": sprint.planned_end_date.isoformat(),
        }
        if sprint
        else None,
        "items": [],
    }
    model = Card if type == "card" else Check
    series = func.coalesce(model.repeat_series_id if model is Card else model.series_id, model.id)
    moment = model.completed_at if model is Card else model.resolved_at
    outcome = model.outcome if model is Check else None
    eligible = select(ScheduleDefinition.entity_id).where(
        ScheduleDefinition.type == type,
        ScheduleDefinition.source_text.is_not(None),
        ScheduleDefinition.submitted_at < end,
        ScheduleDefinition.valid_until.is_(None) | (ScheduleDefinition.valid_until > start),
    )
    latest = (
        select(func.max(model.id))
        .where(series.in_(eligible), series > (after_id or 0))
        .group_by(series)
    )
    current_rows = await session.scalars(select(model).where(model.id.in_(latest)).order_by(series))
    for current in current_rows:
        root = (
            current.repeat_series_id or current.id
            if model is Card
            else current.series_id or current.id
        )
        definitions = list(
            await session.scalars(
                select(ScheduleDefinition).where(
                    ScheduleDefinition.entity_id == root,
                    ScheduleDefinition.type == type,
                )
            )
        )
        floor = period_start(min(start, now, sprint_bounds[0] if sprint else start), "week", tz)
        ceiling = period_end(
            period_start(max(end, now, sprint_bounds[1] if sprint else end), "week", tz), "week", tz
        )
        columns = [model.id, model.schedule_id, model.period_start, moment.label("at")]
        if model is Check:
            columns.append(outcome)
        facts = list(
            (
                await session.execute(
                    select(*columns).where(
                        series == root,
                        moment.is_not(None),
                        ((model.period_start >= floor) & (model.period_start < ceiling))
                        | ((model.period_start.is_(None)) & (moment >= floor) & (moment < ceiling)),
                    )
                )
            ).all()
        )
        range_summary = _summary(definitions, facts, start, end, tz, model is Check)
        if range_summary["planned"] == 0 and range_summary["done"] == 0:
            continue
        aggregate = [func.count().label("done")]
        if model is Check:
            aggregate += [
                func.sum(case((outcome == answer, 1), else_=0)).label(answer)
                for answer in ("passed", "missed")
            ]
        totals = (
            await session.execute(select(*aggregate).where(series == root, moment.is_not(None)))
        ).one()
        definition, next_event = current.schedule_record, None
        open_ = current.completed_at is None if model is Card else current.outcome is None
        rule = definition.rule if definition else None
        active = bool(open_ and definition and definition.valid_until is None and current.schedule)
        if active and definition.status == "ready":
            if rule["kind"] == "quota":
                at = max(
                    current.period_start or period_start(now, rule["period"], tz),
                    period_start(now, rule["period"], tz),
                )
                used = sum(f.schedule_id == definition.id and f.period_start == at for f in facts)
                next_event = {
                    "start_date": at.astimezone(tz).date().isoformat(),
                    "end_date": (
                        period_end(at, rule["period"], tz).astimezone(tz).date() - timedelta(days=1)
                    ).isoformat(),
                    "remaining": max(0, rule["count"] - used),
                }
            elif rule["kind"] == "fixed":
                next_event = {"at": current.period_start.astimezone(tz).isoformat()}
                if current.period_start < now:
                    next_event["overdue"] = True
            else:
                next_event = {"after_completion": True}
        finite = bool(
            rule and rule["kind"] == "fixed" and rule["timing"]["schedule_kind"] == "once"
        )
        total = {"done": totals.done, "remaining": int(active) if finite or not active else None}
        if model is Check:
            total.update(passed=totals.passed, missed=totals.missed)
        item = {
            "id": current.id,
            "series_id": root,
            "title": current.title,
            "schedule": current.schedule,
            "status": definition.status if active else "ended",
            "next": next_event,
            "range": range_summary,
            "sprint": _summary(definitions, facts, *sprint_bounds, tz, model is Check)
            if sprint
            else None,
            "total": total,
        }
        if active and definition.question:
            item["question"] = definition.question
        for field in ("title", "schedule", "question"):
            text = item.get(field)
            if text:
                clipped = text[:DEFAULT_CELL_LIMIT]
                while len(json.dumps(clipped, ensure_ascii=False)) > DEFAULT_CELL_LIMIT:
                    clipped = clipped[: len(clipped) // 2]
                if clipped != text:
                    item[field] = clipped
                    item["text_truncated"] = True
        if len(result["items"]) >= DEFAULT_ROW_LIMIT or (
            len(json.dumps(result, ensure_ascii=False)) + len(json.dumps(item, ensure_ascii=False))
            > DEFAULT_CHAR_BUDGET - 250
        ):
            result["next_after_id"] = result["items"][-1]["series_id"]
            result["notice"] = (
                "More series remain. Call get_scheduled with after_id=next_after_id before reporting complete totals."
            )
            break
        result["items"].append(item)
    return result
