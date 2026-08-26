"""The Sprint: starting one, what it records, and the two ways it ends.

Planning owns the Sprint and every row that says what an Action was worth to it. Cards
calls in here after it writes a stage; Planning never calls Cards back, which is what
keeps the two features acyclic. The archive sweep a Sprint's end sets off is therefore
composed in `domain.py`, where both halves are in reach.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...constants import (
    SPRINT_LENGTH_MAX_DAYS,
    SPRINT_LENGTH_MIN_DAYS,
)
from ...enums import WorkspaceMode
from ...foundation.clock import utcnow
from ...foundation.errors import DomainError
from ...foundation.models import Workspace
from ...foundation.workspace import require_workspace
from ..cards.api import CardStage, action_titles, planned_actions
from ..profile.api import sprint_length_days as _profile_sprint_length_days
from ..reminders.api import create_sprint_reminder, delete_sprint_reminders
from .model import Sprint, SprintCommitment

# How many unfinished Actions the end-of-Sprint summary names before it counts the rest.
SUMMARY_OPEN_TITLES = 5

_SPRINT_ENDS_TOMORROW = (
    "Sprint {number} ends tomorrow, {end_date}. Check what is still open in Sprint and Today, "
    "and help the owner finalize the status of each of those Actions."
)
_SPRINT_ENDS_TODAY = (
    "Sprint {number} ends today, {end_date}. Tell the owner to close it from the 🏃 Sprint "
    "screen; if they do not, Safwa closes it automatically at midnight and whatever is still "
    "open keeps its stage."
)


async def set_sprint_success_criteria(session: AsyncSession, criteria: str) -> Workspace:
    """Store what the next Sprint must achieve. Kept after a Sprint ends, to edit or reuse."""
    workspace = await require_workspace(session)
    clean = criteria.strip()
    if not clean:
        raise DomainError("Success criteria cannot be empty")
    workspace.sprint_success_criteria = clean
    workspace.revision += 1
    return workspace


async def sprint_length_days(session: AsyncSession) -> int:
    return await _profile_sprint_length_days(session)


async def start_sprint(
    session: AsyncSession,
    *,
    success_criteria: str,
    start_date: date | None = None,
    length_days: int | None = None,
) -> Sprint:
    workspace = await require_workspace(session)
    if WorkspaceMode(workspace.mode) is not WorkspaceMode.PLANNING or workspace.active_sprint_id:
        raise DomainError("A Sprint can start only from Planning")
    criteria = success_criteria.strip()
    if not criteria:
        raise DomainError("A Sprint needs Success criteria before it starts")
    cards = await planned_actions(session)
    if not cards:
        raise DomainError("A Sprint needs at least one Action in Sprint or Today before it starts")
    length = length_days if length_days is not None else await sprint_length_days(session)
    if not SPRINT_LENGTH_MIN_DAYS <= length <= SPRINT_LENGTH_MAX_DAYS:
        raise DomainError(
            f"Sprint length must be between {SPRINT_LENGTH_MIN_DAYS} and "
            f"{SPRINT_LENGTH_MAX_DAYS} days"
        )
    # Numbering follows the highest number ever used, so deleting a Sprint cannot
    # produce a duplicate on the unique constraint.
    highest = await session.scalar(select(func.max(Sprint.number))) or 0
    tz = ZoneInfo(workspace.timezone)
    started_at = utcnow()
    start = start_date or started_at.astimezone(tz).date()
    sprint = Sprint(
        number=highest + 1,
        planned_start_date=start,
        planned_end_date=start + timedelta(days=length - 1),
        actual_started_at=started_at,
        success_criteria=criteria,
    )
    session.add(sprint)
    await session.flush()
    await _schedule_sprint_reminders(session, sprint, started_at=started_at, tz=tz)
    for card in cards:
        session.add(
            SprintCommitment(
                sprint_id=sprint.id,
                card_id=card.id,
                effort_snapshot=card.effort_points or 0,
                scope_kind="initial",
            )
        )
    workspace.mode = WorkspaceMode.SPRINT.value
    workspace.active_sprint_id = sprint.id
    workspace.revision += 1
    return sprint


async def _schedule_sprint_reminders(
    session: AsyncSession, sprint: Sprint, *, started_at: datetime, tz: ZoneInfo
) -> None:
    """Warn the owner the day before the Sprint ends, then on its last day.

    Both fire at the clock the Sprint was started at, so a Sprint started at 18:32 keeps
    saying 18:32.  The first one is skipped when the Sprint is too short to have a day
    before its last one.
    """
    clock = started_at.astimezone(tz).time()
    schedule_dates = (
        (sprint.planned_end_date - timedelta(days=1), _SPRINT_ENDS_TOMORROW),
        (sprint.planned_end_date, _SPRINT_ENDS_TODAY),
    )
    for day, template in schedule_dates:
        moment = datetime.combine(day, clock, tzinfo=tz).astimezone(UTC)
        if moment <= started_at:
            continue
        await create_sprint_reminder(
            session,
            instruction=template.format(
                number=sprint.number, end_date=sprint.planned_end_date.isoformat()
            ),
            at_time=clock,
            anchor_at=moment,
            tz=tz,
            sprint_id=sprint.id,
        )


async def finish_sprint(session: AsyncSession, *, reason: str = "finished") -> Sprint:
    """End the running Sprint. What the workspace does about it afterwards is composed
    in `domain.finish_sprint`, which also runs the archive sweep the ending sets off."""
    workspace = await require_workspace(session)
    if not workspace.active_sprint_id:
        raise DomainError("No Sprint is active")
    sprint = await session.get(Sprint, workspace.active_sprint_id)
    if sprint is None:
        raise DomainError("Active Sprint is missing")
    # Its own end reminders have nothing left to announce.
    await delete_sprint_reminders(session, sprint.id)
    sprint.status = "finished"
    sprint.finish_reason = reason
    sprint.actual_ended_at = utcnow()
    workspace.mode = WorkspaceMode.PLANNING.value
    workspace.active_sprint_id = None
    workspace.revision += 1
    await session.flush()
    await _hand_sprint_to_advisor(session, sprint, tz=ZoneInfo(workspace.timezone))
    return sprint


async def _hand_sprint_to_advisor(session: AsyncSession, sprint: Sprint, *, tz: ZoneInfo) -> None:
    """Give the ended Sprint to Safwa as a Reminder that is already due.

    The summary is written here, so Safwa is told how the Sprint went rather than sent
    reading tables for it, and the delivery is the escalation path a fired Reminder
    already takes — one turn, the same gate, and the same retry if it does not land.
    """
    now = utcnow()
    await create_sprint_reminder(
        session,
        instruction=await sprint_summary(session, sprint),
        at_time=now.astimezone(tz).time(),
        anchor_at=now,
        tz=tz,
        sprint_id=sprint.id,
    )


async def sprint_summary(session: AsyncSession, sprint: Sprint) -> str:
    """How the Sprint went, in six lines read off its own record."""
    metrics = await sprint_metrics(session, sprint.id)
    commitments = list(
        await session.scalars(
            select(SprintCommitment).where(SprintCommitment.sprint_id == sprint.id)
        )
    )
    finished = sum(1 for item in commitments if item.result == CardStage.DONE.value)
    cancelled = sum(1 for item in commitments if item.result == CardStage.CANCELLED.value)
    open_ids = [item.card_id for item in commitments if item.result is None]
    open_titles = await action_titles(session, open_ids)
    shown = open_titles[:SUMMARY_OPEN_TITLES]
    if len(open_titles) > len(shown):
        shown.append(f"and {len(open_titles) - len(shown)} more")
    ended = "the owner closed it" if sprint.finish_reason != "expired" else "its end date passed"
    return "\n".join(
        [
            f"Sprint {sprint.number} is over, {sprint.planned_start_date} – "
            f"{sprint.planned_end_date}; {ended}.",
            f"Success criteria: {sprint.success_criteria}",
            f"Effort: committed {metrics['committed']}, added {metrics['added']}, "
            f"removed {metrics['removed']}, done {metrics['completed']}, "
            f"cancelled {metrics['cancelled']}.",
            f"Actions: {finished} finished, {cancelled} cancelled, {len(open_titles)} still open.",
            "Still open: " + (", ".join(shown) if shown else "nothing"),
            "Tell the owner how the Sprint went in a few sentences. Use only the numbers "
            "above. Ask what to do with what is still open, and about the next Sprint. "
            f"End your message with the link [Sprint retro](retro:{sprint.id}).",
        ]
    )


async def sprint_is_due(session: AsyncSession, *, now: datetime | None = None) -> Sprint | None:
    """The running Sprint, once local midnight has passed its planned end date."""
    workspace = await require_workspace(session)
    if not workspace.active_sprint_id:
        return None
    sprint = await session.get(Sprint, workspace.active_sprint_id)
    if sprint is None:
        return None
    tz = ZoneInfo(workspace.timezone)
    deadline = datetime.combine(sprint.planned_end_date + timedelta(days=1), time(0, 0), tzinfo=tz)
    if (now or utcnow()) < deadline:
        return None
    return sprint


async def sprint_metrics(session: AsyncSession, sprint_id: int) -> dict[str, int]:
    items = list(
        await session.scalars(
            select(SprintCommitment).where(SprintCommitment.sprint_id == sprint_id)
        )
    )
    return {
        "committed": sum(i.effort_snapshot for i in items if i.scope_kind == "initial"),
        "added": sum(i.effort_snapshot for i in items if i.scope_kind == "added"),
        "removed": sum(i.effort_snapshot for i in items if i.removed_at is not None),
        "completed": sum(i.effort_snapshot for i in items if i.result == CardStage.DONE.value),
        "cancelled": sum(i.effort_snapshot for i in items if i.result == CardStage.CANCELLED.value),
    }
