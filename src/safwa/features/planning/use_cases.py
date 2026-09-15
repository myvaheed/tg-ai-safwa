"""The Sprint: starting one, what it records, and the two ways it ends.

Planning owns the Sprint and every row that says what an Action was worth to it. Cards
calls in here after it writes a stage, through `api.py`, which is why that door speaks
nothing but Cards' own vocabulary. The ending is composed the other way round: a Sprint
that ends is the clock the archive runs on, so Planning is what asks Cards and Checks to
sweep what has waited long enough.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.cues.queue import add_cue
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError

from ...constants import SPRINT_LENGTH_MAX_DAYS, SPRINT_LENGTH_MIN_DAYS
from ...foundation.workspace import Workspace, WorkspaceMode, require_workspace
from ..cards.api import CardStage, action_titles, effort_label, planned_actions
from ..cards.use_cases import archive_settled_cards
from ..checks.use_cases import archive_settled_checks
from ..profile.api import sprint_length_days as _profile_sprint_length_days
from ..reminders.use_cases import create_sprint_reminder, delete_sprint_reminders
from .model import Sprint, SprintCommitment, SprintStatus, next_sprint_number

# How many Sprint endings a closed Card or Check waits before it leaves the screens.
ARCHIVE_AFTER_SPRINTS = 2
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
    tz = ZoneInfo(workspace.timezone)
    started_at = utcnow()
    start = start_date or started_at.astimezone(tz).date()
    used = await session.scalars(
        select(Sprint.number).where(Sprint.number.startswith(f"{start:%y.%m}-"))
    )
    sprint = Sprint(
        number=next_sprint_number(start, used),
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
        )


async def settled_cutoff(session: AsyncSession) -> datetime | None:
    """When a Sprint ends, what closed on or before this moment has waited long enough."""
    ended = list(
        await session.scalars(
            select(Sprint)
            .where(Sprint.actual_ended_at.is_not(None))
            .order_by(Sprint.number.desc())
            .limit(ARCHIVE_AFTER_SPRINTS + 1)
        )
    )
    if len(ended) <= ARCHIVE_AFTER_SPRINTS:
        return None
    return ended[ARCHIVE_AFTER_SPRINTS].actual_ended_at


async def archive_settled_items(session: AsyncSession) -> tuple[list[int], list[int]]:
    """Take what closed two Sprints ago off the screens, and report what left.

    A Sprint ending is the clock: nothing is archived while the workspace is in Planning,
    and whatever built up there leaves the moment the next Sprint ends.
    """
    cutoff = await settled_cutoff(session)
    if cutoff is None:
        return [], []
    return (
        await archive_settled_cards(session, cutoff),
        await archive_settled_checks(session, cutoff),
    )


async def expire_due_sprint(session: AsyncSession, *, now: datetime | None = None) -> Sprint | None:
    """Close the active Sprint once local midnight has passed its planned end date.

    Unfinished Actions keep their stage: the Sprint ends, the plan does not evaporate.
    """
    if await sprint_is_due(session, now=now) is None:
        return None
    return await finish_sprint(session, reason="expired")


async def finish_sprint(session: AsyncSession, *, reason: str = "finished") -> Sprint:
    """End the running Sprint, and sweep what its ending settled."""
    workspace = await require_workspace(session)
    if not workspace.active_sprint_id:
        raise DomainError("No Sprint is active")
    sprint = await session.get(Sprint, workspace.active_sprint_id)
    if sprint is None:
        raise DomainError("Active Sprint is missing")
    # Its own end reminders have nothing left to announce.
    await delete_sprint_reminders(session)
    sprint.status = SprintStatus.FINISHED.value
    sprint.finish_reason = reason
    sprint.actual_ended_at = utcnow()
    workspace.mode = WorkspaceMode.PLANNING.value
    workspace.active_sprint_id = None
    workspace.revision += 1
    await session.flush()
    # Written here, so Safwa is told how the Sprint went rather than sent reading tables.
    await add_cue(session, text=await sprint_summary(session, sprint))
    await archive_settled_items(session)
    return sprint


async def sprint_summary(session: AsyncSession, sprint: Sprint) -> str:
    """How the Sprint went, in six lines read off its own record."""
    metrics = await sprint_metrics(session, sprint.id)
    commitments = list(
        await session.scalars(
            select(SprintCommitment).where(SprintCommitment.sprint_id == sprint.id)
        )
    )
    finished = sum(1 for item in commitments if item.result == CardStage.DONE.value)
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
            "Effort: "
            + ", ".join(
                f"{name} {effort_label(metrics[key])}"
                for name, key in (
                    ("committed", "committed"),
                    ("added", "added"),
                    ("removed", "removed"),
                    ("done", "completed"),
                )
            )
            + ".",
            f"Actions: {finished} finished, {len(open_titles)} still open.",
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


async def sprint_metrics(session: AsyncSession, sprint_id: int) -> dict[str, float]:
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
    }
