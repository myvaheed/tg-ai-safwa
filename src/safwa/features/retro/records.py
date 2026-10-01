"""What the retro subagent reads of the Sprints that ended: the one a day fell in, each
one's record as its retro keeps it, and the sums and means of several.

Every number here is read off a Sprint's record as it was written when the Sprint ended
(RT-STATS-003). A model of 4B to 12B adds and divides unreliably, so a total or an average
over several Sprints is worked out here and never left to it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.workspace import require_workspace
from ..planning.closing import RetroStatistics
from ..planning.model import Sprint
from ..profile.api import effort_tracking_on

# How many Sprints one read of whole records may name; a sum or a mean takes any number.
RETRO_DATA_MAX = 6

_MET = {True: "yes", False: "no", None: "not marked"}

# The numbers a record carries that add up and average: everything but the dates, the words
# and the share, which is worked out again from its own total.
SUMMED = (
    "effort_taken",
    "effort_done",
    "effort_initial",
    "effort_added",
    "effort_removed",
    "actions_taken",
    "actions_finished",
    "actions_remaining",
    "actions_blocked",
    "key_actions",
    "key_actions_finished",
    "capacity",
    "tracked_minutes",
    "actions_with_time",
    "criteria_met",
)


def retro_link(sprint: Sprint) -> str:
    return f"[Sprint {sprint.number} retro](retro:{sprint.id})"


async def ended_sprints(session: AsyncSession, limit: int | None = None) -> list[Sprint]:
    """The Sprints that ended, the newest first."""
    query = (
        select(Sprint)
        .where(Sprint.retro.is_not(None))
        .order_by(Sprint.actual_ended_at.desc(), Sprint.id.desc())
    )
    return list(await session.scalars(query if limit is None else query.limit(limit)))


def sprint_record(sprint: Sprint, *, effort_tracking: bool = False) -> dict[str, Any]:
    """One ended Sprint as the model reads it: what its retro screen shows, without the
    rows by day."""
    statistics = RetroStatistics.from_record(sprint.retro)
    record: dict[str, Any] = {
        "id": sprint.id,
        "link": retro_link(sprint),
        "ran": f"{statistics.first_day.isoformat()} – {statistics.last_day.isoformat()}",
        "planned": f"{sprint.planned_start_date.isoformat()} – "
        f"{sprint.planned_end_date.isoformat()}",
        "ended": "its end date passed"
        if sprint.finish_reason == "expired"
        else "the user finished it",
        "success_criteria": sprint.success_criteria,
        "criteria_met": _MET[sprint.criterion_met],
        "actions_taken": statistics.planned,
        "actions_finished": statistics.finished,
        "actions_remaining": statistics.remaining,
        "actions_blocked": statistics.blocked,
        "key_actions": statistics.key_total,
        "key_actions_finished": statistics.key_finished,
    }
    if effort_tracking:
        record["unestimated_actions"] = statistics.unestimated
        record["capacity"] = sprint.capacity_effort_points if sprint.capacity_effort_points is not None else "off"
        if not statistics.unestimated:
            record.update(
                effort_taken=statistics.taken, effort_done=statistics.done,
                done_share_percent=statistics.done_share,
                effort_initial=statistics.initial, effort_added=statistics.added,
                effort_removed=statistics.removed,
            )
        else:
            record["effort"] = "Not fully estimated; use Action counts."
    if statistics.time_tracking:
        record["tracked_minutes"] = statistics.minutes
        record["actions_with_time"] = statistics.timed
    else:
        record["time"] = "not tracked"
    if sprint.analysis:
        record["analysis_headline"] = sprint.analysis.get("headline", "")
        record["experiment"] = sprint.analysis.get("experiment", "")
    return record


def _normalized(number: str) -> str:
    """The number alone, when it came with the word Sprint before it."""
    text = str(number).strip()
    return text[len("sprint") :].strip() if text.lower().startswith("sprint") else text


async def records_by_number(
    session: AsyncSession, numbers: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Each named Sprint's record by its number, or why there is none."""
    wanted = [_normalized(number) for number in numbers]
    rows = {
        sprint.number: sprint
        for sprint in await session.scalars(select(Sprint).where(Sprint.number.in_(wanted)))
    }
    found: dict[str, dict[str, Any]] = {}
    effort_tracking = await effort_tracking_on(session)
    for number in wanted:
        sprint = rows.get(number)
        if sprint is None:
            found[number] = {"error": f"No Sprint has the number {number}."}
        elif sprint.retro is None:
            found[number] = {
                "error": f"Sprint {number} is still running: its retro is written when it ends."
            }
        else:
            found[number] = sprint_record(sprint, effort_tracking=effort_tracking)
    return found


def aggregate(
    records: dict[str, dict[str, Any]], op: Literal["sum", "mean"]
) -> dict[str, Any]:
    """The sum or the mean of every number the named records carry, as one object.

    Met counts as 1 and Not met as 0, so their sum is how many were met and their mean the
    share. A field some Sprints lack — capacity off, time not tracked, no mark — is worked
    out over the ones that have it, and `counted_over` says over how many. The share of the
    effort finished is the finished total over the taken total, whichever the operation.
    """
    included = {number: record for number, record in records.items() if "error" not in record}
    values: dict[str, float] = {}
    counted_over: dict[str, int] = {}
    for field in SUMMED:
        numbers = [_number(record.get(field)) for record in included.values()]
        present = [value for value in numbers if value is not None]
        if not present:
            continue
        total = sum(present)
        values[field] = round(total if op == "sum" else total / len(present), 2)
        if len(present) < len(included):
            counted_over[field] = len(present)
    estimated = [record for record in included.values() if "effort_taken" in record]
    taken = sum(record["effort_taken"] for record in estimated)
    done = sum(record["effort_done"] for record in estimated)
    result: dict[str, Any] = {
        "op": op,
        "sprints": [record["link"] for record in included.values()],
        "sprint_count": len(included),
        "values": values,
    }
    if estimated:
        result["done_share_percent"] = round(100 * done / taken) if taken else 0
        if len(estimated) < len(included):
            counted_over["done_share_percent"] = len(estimated)
    if counted_over:
        result["counted_over"] = counted_over
    errors = {number: record["error"] for number, record in records.items() if "error" in record}
    if errors:
        result["errors"] = errors
    return result


def _number(value: Any) -> float | None:
    """A record's value as a number, Met as 1 and Not met as 0, or None where it has none."""
    if value == "yes":
        return 1
    if value == "no":
        return 0
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


async def sprint_on(session: AsyncSession, day: date, today: date) -> dict[str, Any]:
    """The ended Sprint whose days hold this one, or why there is none.

    A Sprint's days run from the day it started to the day it ended or its planned end,
    whichever came first, as its record counts them (RT-STATS-003).
    """
    for sprint in await ended_sprints(session):
        statistics = RetroStatistics.from_record(sprint.retro)
        if statistics.first_day <= day <= statistics.last_day:
            return {
                "number": sprint.number,
                "id": sprint.id,
                "link": retro_link(sprint),
                "ran": f"{statistics.first_day.isoformat()} – {statistics.last_day.isoformat()}",
            }
    workspace = await require_workspace(session)
    running = (
        await session.get(Sprint, workspace.active_sprint_id)
        if workspace.active_sprint_id
        else None
    )
    if running is not None and running.planned_start_date <= day <= today:
        return {
            "error": f"On {day.isoformat()} Sprint {running.number} was running, and it still "
            "is: its retro is written when it ends."
        }
    return {"error": f"No Sprint was running on {day.isoformat()}."}
