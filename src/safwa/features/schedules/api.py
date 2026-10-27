"""The shared boundary for a source edit, an occurrence and a read-only plan."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import DEFAULT_CELL_LIMIT, DEFAULT_CHAR_BUDGET, DEFAULT_ROW_LIMIT
from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.days import day_words, moment_words
from tg_agent_shell.foundation.errors import DomainError

from ...constants import REMIND_LEAD_MAX_MINUTES
from ...foundation.workspace import require_workspace
from ..cards.model import Card, CardKind
from ..checks.model import Check
from ..reminders.api import Schedule, describe, lead_words, repeat_days, schedule_from_payload
from ..reminders.model import Reminder, ScheduleKind
from .model import ScheduleDefinition
from .rules import (
    current_slot,
    deadline_moment,
    first_slot,
    next_slot,
    past_until,
    period_end,
    period_start,
    repeats,
    windows,
)

SCHEDULE_INSTRUCTION = "Describe the schedule, e.g. every evening, every Monday, five times a day, or Tuesday at 15:00. Send off to clear it."
DEADLINE_INSTRUCTION = "Send the deadline, e.g. 20 October, end of next month, or 20.10.2026 18:00. Send off to clear it."
# The longest date range one get_scheduled call reads.
SCHEDULED_RANGE_DAYS_MAX = 93
# More executions a day than this are a Check's to observe, not an Action's to do.
ACTION_DAILY_EXECUTIONS_MAX = 5
# The words of the Reminder that Remind makes; the Cue adds the Schedule it fires on.
REMIND_TEXT = "{label} #{id} «{title}»"
REMIND_NEEDS_TIME = "Remind needs a Schedule with a time still ahead."
REMIND_LEAD_RANGE = f"Remind takes 0 to {REMIND_LEAD_MAX_MINUTES} minutes before its time."
# The change a hook follows up on: an item's Schedule ran its total or reached its last day, so
# no next instance opened. Its data names which: "total" or "last_day".
SCHEDULE_ENDED = "schedule.ended"

type ScheduleTarget = Literal["action", "check", "deadline"]


async def workspace_zone(session: AsyncSession) -> ZoneInfo:
    return ZoneInfo((await require_workspace(session)).timezone)


def item_type(model: type[Card] | type[Check]) -> str:
    """How a Schedule revision and a Reminder name a Card or a Check."""
    return "card" if model is Card else "check"


def item_label(entity: Card | Check) -> str:
    return entity.kind.capitalize() if isinstance(entity, Card) else "Check"


def is_open(entity: Card | Check) -> bool:
    """An Action or Goal not yet finished, a Check not yet answered."""
    return (entity.completed_at if isinstance(entity, Card) else entity.outcome) is None


def schedule_target(entity: Card | Check) -> ScheduleTarget:
    """What the text is compiled into: a Goal's or Subgoal's Schedule is its Deadline."""
    if isinstance(entity, Check):
        return "check"
    return "action" if entity.kind == CardKind.ACTION.value else "deadline"


async def set_schedule(
    session: AsyncSession,
    entity: Card | Check,
    text: str | None,
    rule: dict[str, Any] | None,
) -> None:
    """Write a new revision with the rule its text was read as, or clear the Schedule."""
    text = (text or "").strip() or None
    if text and text.casefold() == "off":
        text = None
    current = entity.schedule_record
    if entity.schedule == text and (current.rule if current else None) == (rule if text else None):
        return
    if entity.is_closed_repeat():
        raise DomainError("Edit Schedule on the current instance of this series")
    if not is_open(entity):
        raise DomainError(
            "Edit Schedule on an open Card or Pending Check to preserve the completed instance"
        )
    type_ = item_type(type(entity))
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
    entity.schedule = text
    entity.period_start = None
    if text is None:
        entity.schedule_record = None
        return
    definition = ScheduleDefinition(
        entity_id=series_id,
        type=type_,
        source_text=text,
        submitted_at=now,
        rule=rule,
    )
    session.add(definition)
    await session.flush()
    entity.schedule_record = definition
    await assign_first(session, entity)


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
            ScheduleDefinition.type == item_type(model),
            ScheduleDefinition.entity_id.in_(roots - remaining),
            ScheduleDefinition.valid_until.is_(None),
        )
    ):
        definition.valid_until = utcnow()


async def occurrence_count(session: AsyncSession, entity: Card | Check) -> int:
    return await _answered_count(session, entity, type(entity).period_start == entity.period_start)


async def total_count(session: AsyncSession, entity: Card | Check) -> int:
    """The instances answered under the entity's Schedule revision: what its total counts."""
    return await _answered_count(session, entity)


async def _answered_count(session: AsyncSession, entity: Card | Check, *where: Any) -> int:
    model = type(entity)
    answered = (
        Card.completed_at.is_not(None) if isinstance(entity, Card) else Check.outcome.is_not(None)
    )
    return int(
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.schedule_id == entity.schedule_id, answered, *where)
        )
        or 0
    )


async def successor_slot(
    session: AsyncSession, entity: Card | Check
) -> tuple[bool, datetime | None]:
    """Whether a next instance opens, and its slot. One that ran its total, or whose next slot
    falls after the last day, opens none."""
    definition = entity.schedule_record
    if definition is None or not definition.rule:
        return False, None
    await session.flush()
    rule, tz, now = definition.rule, await workspace_zone(session), utcnow()
    limit = rule.get("limit")
    if limit is not None and await total_count(session, entity) >= limit:
        return _ended(session, entity, "total")
    opens, slot = next_slot(
        rule, entity.period_start, now, await occurrence_count(session, entity), tz
    )
    if opens and past_until(rule, slot or now, tz):
        return _ended(session, entity, "last_day")
    return opens, slot


def _ended(session: AsyncSession, entity: Card | Check, reason: str) -> tuple[bool, None]:
    record_change(
        session, SCHEDULE_ENDED, entity.id, data={"type": item_type(type(entity)), "reason": reason}
    )
    return False, None


async def restart_slot(
    session: AsyncSession, entity: Card | Check, rule: dict[str, Any] | None
) -> tuple[bool, datetime | None]:
    """Whether a Schedule written on this instance starts its series again, and the first
    slot of its next instance: the newest instance of a repeating series, closed, so nothing
    in the series is open."""
    definition = entity.schedule_record
    if (
        not rule
        or is_open(entity)
        or definition is None
        or not definition.rule
        or not repeats(definition.rule)
    ):
        return False, None
    model = type(entity)
    series = func.coalesce(model.repeat_series_id if model is Card else model.series_id, model.id)
    root = (entity.repeat_series_id if model is Card else entity.series_id) or entity.id
    newer = await session.scalar(
        select(model.id).where(series == root, model.id > entity.id).limit(1)
    )
    if newer is not None:
        return False, None
    return True, first_slot(rule, utcnow(), await workspace_zone(session))


async def prepare_occurrence(session: AsyncSession, entity: Card | Check) -> None:
    """A closing belongs to the period it happens in, or the first one when it happens before
    the Schedule starts, so an early one takes nothing from the next period (SCH-PERIOD-031)."""
    definition = entity.schedule_record
    if definition and definition.rule:
        slot = current_slot(definition.rule, utcnow(), await workspace_zone(session))
        if slot is not None:
            entity.period_start = slot


async def move_on_open_instances(session: AsyncSession, *, now: datetime | None = None) -> None:
    """Put each open instance of a repeating Schedule on the period it is in, once that period
    starts after its own: the same instance, with nothing written for the period it leaves.

    Remind's Reminder counts from the instance's moment, so it moves with it and keeps its next
    fire: a later rename then finds its timing unchanged and does not fire it again."""
    now = now or utcnow()
    tz = await workspace_zone(session)
    for model in (Card, Check):
        open_ = model.completed_at.is_(None) if model is Card else model.outcome.is_(None)
        for entity in await session.scalars(
            select(model).where(
                open_,
                model.archived_at.is_(None),
                model.schedule_id.is_not(None),
                model.period_start.is_not(None),
            )
        ):
            rule = entity.schedule_record.rule
            if not rule or not repeats(rule):
                continue
            slot = current_slot(rule, now, tz)
            if slot is None or slot <= entity.period_start or past_until(rule, slot, tz):
                continue
            entity.period_start = slot
            reminder = await entity_reminder(session, entity)
            if reminder is not None:
                reminder.anchor_at = slot


async def assign_first(session: AsyncSession, entity: Card | Check) -> None:
    definition = entity.schedule_record
    if definition:
        entity.period_start = first_slot(
            definition.rule, definition.submitted_at, await workspace_zone(session)
        )


async def scheduled_stage(
    session: AsyncSession, card: Card, slot: datetime | None, previous_stage: str
) -> str:
    """Where a generated Action opens. A slot due today goes to Today and a later one to
    the running Sprint while the Sprint holds it. In Planning a copy from Backlog stays there,
    and a later copy of planned work stays planned for the next Sprint."""
    from ..planning.model import Sprint

    workspace = await require_workspace(session)
    end = await session.scalar(
        select(Sprint.planned_end_date).where(Sprint.id == workspace.active_sprint_id)
    )
    planned = previous_stage in {"today", "sprint"}
    if end is None and not planned:
        return "backlog"
    if slot is None:
        return previous_stage if planned else "sprint"
    tz = await workspace_zone(session)
    day = slot.astimezone(tz).date()
    rule = card.schedule_record.rule
    longer = rule["kind"] == "quota" and rule["period"] != "day"
    if not longer and day <= utcnow().astimezone(tz).date():
        return "today"
    return "sprint" if end is None or day <= end else "backlog"


async def schedule_summary(session: AsyncSession, entity: Card | Check) -> str | None:
    """How the Schedule was understood, for the screen that shows it."""
    definition = entity.schedule_record
    if definition is None:
        return None
    rule, tz = definition.rule, await workspace_zone(session)
    label = "completed" if isinstance(entity, Card) else "answered"
    if rule["kind"] == "quota":
        start = entity.period_start.astimezone(tz)
        summary = (
            f"{await occurrence_count(session, entity)}/{rule['count']} {label} "
            f"for the {rule['period']} from {start:%d.%m.%Y}."
        )
        if rule.get("until"):
            summary += f" Last day {date.fromisoformat(rule['until']):%d.%m.%Y}."
    elif rule["kind"] != "fixed":
        summary = rule_summary(rule, tz)
    else:
        overdue = " It is overdue." if is_open(entity) and entity.period_start < utcnow() else ""
        summary = (
            f"{rule_summary(rule, tz)} Appointment: "
            f"{appointment_label(rule, entity.period_start, tz)}.{overdue}"
        )
    if rule.get("limit"):
        summary += f" {await total_count(session, entity)}/{rule['limit']} {label} so far."
    return summary


def remind_timing(entity: Card | Check, now: datetime, lead: int = 0) -> Schedule | None:
    """The Reminder timing Remind gives an open item: its Schedule's clock, counted from the
    open instance's moment, so a copy finished early is reminded at its next appointment, and
    firing `lead` minutes before each moment. None when the Schedule has no clock, or a
    one-time moment has passed."""
    definition = entity.schedule_record
    if definition is None or not is_open(entity):
        return None
    timing = clock_timing(definition.rule)
    if timing is None or (not timing.repeating and entity.period_start <= now):
        return None
    return replace(timing, anchor_at=entity.period_start, lead_minutes=lead)


def clock_timing(rule: dict[str, Any]) -> Schedule | None:
    """The timing of a rule's clock: a Deadline's time or a fixed Schedule's; None without one."""
    if rule["kind"] == "deadline" and rule["time"]:
        return Schedule(kind=ScheduleKind.ONCE, at_time=time.fromisoformat(rule["time"]))
    if rule["kind"] == "fixed" and not rule.get("all_day"):
        return schedule_from_payload(rule["timing"])
    return None


def remind_label(lead: int | None) -> str:
    """Remind as the screens say it: Off, At the time, or how long before."""
    if lead is None:
        return "Off"
    return f"{lead_words(lead)} before" if lead else "At the time"


def remind_text(entity: Card | Check) -> str:
    return REMIND_TEXT.format(label=item_label(entity), id=entity.id, title=entity.title)


async def entity_reminder(session: AsyncSession, entity: Card | Check) -> Reminder | None:
    """The Reminder Remind made for this item, if it is on."""
    return await session.scalar(
        select(Reminder).where(
            Reminder.item_type == item_type(type(entity)), Reminder.item_id == entity.id
        )
    )


def appointment_label(
    rule: dict[str, Any], at: datetime, tz: ZoneInfo, day_format: str = "%a %d.%m"
) -> str:
    """An appointment's moment, or only its day when it has no clock."""
    local = at.astimezone(tz)
    return f"{local:{day_format}}" if rule.get("all_day") else f"{local:{day_format} %H:%M}"


def appointment_words(rule: dict[str, Any], at: datetime, tz: ZoneInfo, today: date) -> str:
    """An appointment's moment as a model is told it, or only its day when it has no
    clock (WS-CONTEXT-014)."""
    local = at.astimezone(tz)
    return day_words(local.date(), today) if rule.get("all_day") else moment_words(local, today)


def rule_summary(rule: dict[str, Any], tz: ZoneInfo) -> str:
    """A compiled rule in words."""
    words = _rule_words(rule, tz) + _bounds_words(rule)
    return f"{words}, {rule['limit']} times in total." if rule.get("limit") else f"{words}."


def _bounds_words(rule: dict[str, Any]) -> str:
    """A quota's first day and the last day, as the screens say them."""
    words = f" from {date.fromisoformat(rule['start']):%d.%m.%Y}" if rule.get("start") else ""
    return words + (f", until {date.fromisoformat(rule['until']):%d.%m.%Y}" if rule.get("until") else "")


def _rule_words(rule: dict[str, Any], tz: ZoneInfo) -> str:
    if rule["kind"] == "deadline":
        return f"Due by {deadline_moment(rule, tz).astimezone(tz):%d.%m.%Y %H:%M}"
    if rule["kind"] == "after_completion":
        return "Repeats after each completion"
    if rule["kind"] == "quota":
        return f"{rule['count']} per {rule['period']}"
    timing = schedule_from_payload(rule["timing"])
    if not rule.get("all_day"):
        return _first_upper(describe(timing, tz=tz))
    if not timing.repeating:
        return f"On {timing.anchor_at.astimezone(tz):%a %d.%m.%Y}, any time that day"
    return _first_upper(f"{repeat_days(timing)}, any time that day")


def _first_upper(text: str) -> str:
    # str.capitalize would lower the weekdays: "Every mon".
    return text[:1].upper() + text[1:]


async def planned_executions(
    session: AsyncSession, cards: Sequence[Card], start_date: date, end_date: date
) -> dict[int, int | None]:
    """The executions each open Action has left from today, or a later start, through the
    end date: 1 without a Schedule, None while unknown, and at least 1 for the Action itself.

    Each quota period adds its share for the days covered, never more than its quota has left,
    and the whole never more than the Schedule's total has left.
    """
    tz = await workspace_zone(session)
    start, end = _date_bounds(max(start_date, utcnow().astimezone(tz).date()), end_date, tz)
    counts: dict[int, int | None] = {}
    periods: dict[int, list[tuple[datetime, datetime, int]]] = {}
    for card in cards:
        definition = card.schedule_record
        if definition is None:
            counts[card.id] = 1
        elif definition.rule["kind"] == "after_completion":
            counts[card.id] = None
        else:
            floor = start if definition.rule["kind"] == "quota" else max(start, definition.submitted_at)
            periods[card.id] = windows(definition.rule, floor, end, tz)
    done: dict[tuple[int, datetime], int] = {}
    bounds = [period for found in periods.values() for period in found]
    if bounds:
        rows = await session.execute(
            select(Card.schedule_id, Card.period_start, func.count())
            .where(
                Card.schedule_id.in_([card.schedule_id for card in cards if card.id in periods]),
                Card.completed_at.is_not(None),
                Card.period_start >= min(at for at, _, _ in bounds),
                Card.period_start < max(until for _, until, _ in bounds),
            )
            .group_by(Card.schedule_id, Card.period_start)
        )
        done = {(schedule_id, slot): count for schedule_id, slot, count in rows}
    for card in cards:
        if card.id not in periods:
            continue
        rule = card.schedule_record.rule
        count = 0
        for at, until, share in periods[card.id]:
            used = sum(
                n for (schedule_id, slot), n in done.items()
                if schedule_id == card.schedule_id and at <= slot < until
            )
            limit = rule["count"] if rule["kind"] == "quota" else share
            count += max(0, min(share, limit - used))
        if rule["kind"] == "fixed" and card.period_start is not None and card.period_start < start:
            count += 1
        if rule.get("limit"):
            count = min(count, rule["limit"] - await total_count(session, card))
        counts[card.id] = max(1, count)
    return counts


async def remaining_occurrences(
    session: AsyncSession, card: Card, start_date: date, end_date: date
) -> int | None:
    return (await planned_executions(session, [card], start_date, end_date))[card.id]


async def checks_today(
    session: AsyncSession, *, now: datetime | None = None,
) -> list[tuple[Check, int]]:
    """Pending Checks with appointments or daily quota left on the local day."""
    tz = await workspace_zone(session)
    now = now or utcnow()
    start = period_start(now, "day", tz)
    end = period_end(start, "day", tz)
    found = []
    for check in await session.scalars(
        select(Check).where(
            Check.outcome.is_(None), Check.archived_at.is_(None), Check.schedule_id.is_not(None),
        ).order_by(Check.period_start, Check.id)
    ):
        rule = check.schedule_record.rule
        if past_until(rule, now, tz) or check.period_start is None or check.period_start >= end:
            continue
        if rule["kind"] == "quota" and rule["period"] == "day":
            count = rule["count"] - await _answered_count(
                session, check, Check.period_start >= start, Check.period_start < end,
            )
        elif rule["kind"] == "fixed":
            count = sum(n for _, _, n in windows(rule, max(now, check.period_start), end, tz))
            count += int(start <= check.period_start < now)
        else:
            continue
        if rule.get("limit") is not None:
            count = min(count, rule["limit"] - await total_count(session, check))
        if count > 0:
            found.append((check, count))
    return found


def _date_bounds(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time(), tzinfo=tz).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time(), tzinfo=tz).astimezone(UTC),
    )


def _summary(
    definitions: list[ScheduleDefinition],
    facts: list[Any],
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    checks: bool,
) -> dict[str, Any]:
    """Planned and done in [start, end): an appointment counts on its date, any other fact
    when it happened."""
    planned: int | None = 0
    for definition in definitions:
        floor, ceiling = max(start, definition.submitted_at), min(end, definition.valid_until or end)
        if floor >= ceiling:
            continue
        if definition.rule["kind"] == "after_completion":
            planned = None
        elif planned is not None:
            planned += sum(share for _, _, share in windows(definition.rule, floor, ceiling, tz))
    fixed = {d.id for d in definitions if d.rule and d.rule["kind"] == "fixed"}
    selected = [
        fact for fact in facts
        if start <= (fact.period_start if fact.schedule_id in fixed and fact.period_start else fact.at) < end
    ]
    result: dict[str, Any] = {
        "planned": planned,
        "done": len(selected),
        "remaining": None if planned is None else max(0, planned - len(selected)),
    }
    if checks:
        result.update(
            passed=sum(fact.outcome == "passed" for fact in selected),
            missed=sum(fact.outcome == "missed" for fact in selected),
        )
    return result


def _next_event(
    current: Card | Check, definition: ScheduleDefinition, used: int, now: datetime, tz: ZoneInfo
) -> dict[str, Any]:
    rule = definition.rule
    if rule["kind"] == "quota":
        at = max(current.period_start, period_start(now, rule["period"], tz))
        return {
            "start_date": at.astimezone(tz).date().isoformat(),
            "end_date": (
                period_end(at, rule["period"], tz).astimezone(tz).date() - timedelta(days=1)
            ).isoformat(),
            "remaining": max(0, rule["count"] - used),
        }
    if rule["kind"] == "after_completion":
        return {"after_completion": True}
    local = current.period_start.astimezone(tz)
    event: dict[str, Any] = (
        {"date": local.date().isoformat()} if rule.get("all_day") else {"at": local.isoformat()}
    )
    if current.period_start < now:
        event["overdue"] = True
    return event


def _clip(item: dict[str, Any]) -> None:
    for field in ("title", "schedule"):
        text = item.get(field)
        if not text:
            continue
        clipped = text[:DEFAULT_CELL_LIMIT]
        while len(json.dumps(clipped, ensure_ascii=False)) > DEFAULT_CELL_LIMIT:
            clipped = clipped[: len(clipped) // 2]
        if clipped != text:
            item[field] = clipped
            item["text_truncated"] = True


async def get_scheduled(
    session: AsyncSession,
    start_date: date,
    end_date: date,
    type: str,
    *,
    after_id: int | None = None,
) -> dict[str, Any]:
    """One bounded summary per scheduled series of Actions or Checks; revisions keep the
    meaning of old facts."""
    if type not in {"card", "check"} or not 0 <= (end_date - start_date).days < SCHEDULED_RANGE_DAYS_MAX:
        raise DomainError(
            f"Use card or check and an ordered date range of at most {SCHEDULED_RANGE_DAYS_MAX} days"
        )
    from ..planning.model import Sprint

    workspace = await require_workspace(session)
    tz, now = ZoneInfo(workspace.timezone), utcnow()
    start, end = _date_bounds(start_date, end_date, tz)
    sprint = (
        await session.get(Sprint, workspace.active_sprint_id) if workspace.active_sprint_id else None
    )
    sprint_bounds = (
        _date_bounds(sprint.planned_start_date, sprint.planned_end_date, tz) if sprint else None
    )
    result: dict[str, Any] = {
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
    eligible = select(ScheduleDefinition.entity_id).where(
        ScheduleDefinition.type == type,
        ScheduleDefinition.submitted_at < end,
        ScheduleDefinition.valid_until.is_(None) | (ScheduleDefinition.valid_until > start),
    )
    latest = select(func.max(model.id)).where(series.in_(eligible), series > (after_id or 0))
    if model is Card:
        # A Goal's or Subgoal's Schedule is its Deadline, which plans no work.
        latest = latest.where(Card.kind == CardKind.ACTION.value)
    lo, hi = (min(start, sprint_bounds[0]), max(end, sprint_bounds[1])) if sprint else (start, end)
    columns = [model.id, model.schedule_id, model.period_start, moment.label("at")]
    if model is Check:
        columns.append(Check.outcome)
    for current in await session.scalars(
        select(model).where(model.id.in_(latest.group_by(series))).order_by(series)
    ):
        root = (current.repeat_series_id if model is Card else current.series_id) or current.id
        definitions = list(
            await session.scalars(
                select(ScheduleDefinition).where(
                    ScheduleDefinition.entity_id == root, ScheduleDefinition.type == type
                )
            )
        )
        facts = list(
            await session.execute(
                select(*columns).where(
                    series == root,
                    moment.is_not(None),
                    ((model.period_start >= lo) & (model.period_start < hi))
                    | ((moment >= lo) & (moment < hi)),
                )
            )
        )
        range_summary = _summary(definitions, facts, start, end, tz, model is Check)
        if range_summary["planned"] == 0 and range_summary["done"] == 0:
            continue
        aggregate = [func.count().label("done")]
        if model is Check:
            aggregate += [
                func.sum(case((Check.outcome == answer, 1), else_=0)).label(answer)
                for answer in ("passed", "missed")
            ]
        totals = (
            await session.execute(select(*aggregate).where(series == root, moment.is_not(None)))
        ).one()
        definition = current.schedule_record
        open_ = current.completed_at is None if model is Card else current.outcome is None
        active = bool(open_ and definition and definition.valid_until is None)
        next_event = None
        if active:
            rule, used = definition.rule, 0
            if rule["kind"] == "quota":
                current_period = max(current.period_start, period_start(now, rule["period"], tz))
                used = await session.scalar(
                    select(func.count()).where(
                        model.schedule_id == definition.id,
                        model.period_start == current_period,
                        moment.is_not(None),
                    )
                )
            next_event = _next_event(current, definition, used, now, tz)
        once = bool(definition and definition.rule.get("timing", {}).get("schedule_kind") == "once")
        remaining = int(active) if once or not active else None
        if active and definition.rule.get("limit"):
            remaining = definition.rule["limit"] - await total_count(session, current)
        total: dict[str, Any] = {"done": totals.done, "remaining": remaining}
        if model is Check:
            total.update(passed=totals.passed, missed=totals.missed)
        item = {
            "id": current.id,
            "series_id": root,
            "title": current.title,
            "schedule": current.schedule,
            "status": "active" if active else "ended",
            "next_at": next_event,
            "range": range_summary,
            "sprint": _summary(definitions, facts, *sprint_bounds, tz, model is Check) if sprint else None,
            "total": total,
        }
        _clip(item)
        if result["items"] and (
            len(result["items"]) >= DEFAULT_ROW_LIMIT
            or len(json.dumps(result, ensure_ascii=False)) + len(json.dumps(item, ensure_ascii=False))
            > DEFAULT_CHAR_BUDGET - 250
        ):
            result["next_after_id"] = result["items"][-1]["series_id"]
            result["notice"] = "More series remain. Call get_scheduled with after_id=next_after_id."
            break
        result["items"].append(item)
    return result
