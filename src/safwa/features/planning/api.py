"""What another feature may ask of Planning.

Cards calls the commitment operations after it has written a stage or deleted a Card: the
commitment rows are the Sprint's, and Cards never touches one itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.clock import utcnow

from ...foundation.workspace import Workspace, WorkspaceMode, require_workspace
from ..cards.api import (
    PLANNED_STAGES,
    SCHEDULE_NOTICE_DAYS,
    TERMINAL_STAGES,
    Card,
    CardStage,
    actions_on_stages,
    planned_actions,
)
from ..cards.model import CardKind, Priority
from ..profile.api import sprint_length_days
from ..schedules.api import planned_executions, remaining_occurrences
from .model import Sprint, SprintCommitment

# The stages an Action has to be on for a Sprint to have anything to say about it.
SPRINT_SCOPE = frozenset({CardStage.SPRINT, CardStage.TODAY, CardStage.DONE})

# The changes a hook may follow up on: a Sprint started, by hand or from a proposal, and
# one ended, by hand or at the midnight after its planned last day; an Action that joined
# the running Sprint, or left it unfinished; and the Sprint's key Actions marked.
SPRINT_STARTED = "sprint.started"
SPRINT_ENDED = "sprint.ended"
SPRINT_JOINED = "sprint.joined"
SPRINT_LEFT = "sprint.left"
SPRINT_KEY_ACTIONS = "sprint.key_actions"


async def sprint_is_active(session: AsyncSession) -> bool:
    """Whether a Sprint is running."""
    workspace = await session.get(Workspace, 1)
    return bool(workspace and workspace.active_sprint_id)


async def criteria_refusal(session: AsyncSession, criteria: str) -> str | None:
    """Why these Success criteria cannot be written now, or None when they can. A running
    Sprint's were fixed when it started, so only the next one's are written, in Planning."""
    workspace = await require_workspace(session)
    if workspace.active_sprint_id:
        return "A Sprint is running: its Success criteria were fixed when it started"
    if not criteria.strip():
        return "Success criteria cannot be empty"
    return None


async def start_refusal(session: AsyncSession, criteria: str) -> str | None:
    """Why a Sprint cannot start now with these Success criteria, or None when it can."""
    workspace = await require_workspace(session)
    if WorkspaceMode(workspace.mode) is not WorkspaceMode.PLANNING or workspace.active_sprint_id:
        return "A Sprint can start only from Planning"
    if not criteria.strip():
        return "A Sprint needs Success criteria before it starts"
    if not await planned_actions(session):
        return "A Sprint needs at least one Action in Sprint or Today before it starts"
    return None


async def active_sprint_end_date(session: AsyncSession) -> date | None:
    """The planned last day of the running Sprint, or None in Planning."""
    workspace = await require_workspace(session)
    sprint = await session.get(Sprint, workspace.active_sprint_id) if workspace.active_sprint_id else None
    return sprint.planned_end_date if sprint else None


def sprint_day(sprint: Sprint, today: date) -> tuple[int, int]:
    """Which day of how many the Sprint is on, its first day being day 1."""
    return (
        (today - sprint.planned_start_date).days + 1,
        (sprint.planned_end_date - sprint.planned_start_date).days + 1,
    )


@dataclass(frozen=True)
class PlanLoad:
    counts: dict[int, int | None]
    actions: int
    effort: float
    unestimated: int
    unknown_schedules: int


async def plan_load(
    session: AsyncSession, cards: list[Card],
    *, start_date: date | None = None, end_date: date | None = None,
) -> PlanLoad:
    """What is left of the running Sprint from today, or of the Profile's next Sprint
    starting today."""
    if start_date is None:
        workspace = await require_workspace(session)
        sprint = await session.get(Sprint, workspace.active_sprint_id) if workspace.active_sprint_id else None
        start_date = sprint.planned_start_date if sprint else utcnow().astimezone(ZoneInfo(workspace.timezone)).date()
        end_date = sprint.planned_end_date if sprint else start_date + timedelta(days=await sprint_length_days(session) - 1)
    counts = await planned_executions(session, cards, start_date, end_date)
    quantities = {card_id: count or 0 for card_id, count in counts.items()}
    return PlanLoad(
        counts=counts,
        actions=sum(quantities.values()),
        effort=sum((card.effort_points or 0) * quantities[card.id] for card in cards),
        unestimated=sum(quantities[card.id] for card in cards if card.effort_points is None),
        unknown_schedules=sum(count is None for count in counts.values()),
    )


async def sync_commitment_for_stage(
    session: AsyncSession, card: Card, previous_stage: CardStage | None = None
) -> None:
    """Record what a stage change means for the running Sprint."""
    workspace = await require_workspace(session)
    if not workspace.active_sprint_id or card.kind != CardKind.ACTION.value:
        return
    commitment = await session.scalar(
        select(SprintCommitment).where(
            SprintCommitment.sprint_id == workspace.active_sprint_id,
            SprintCommitment.card_id == card.id,
        )
    )
    current = CardStage(card.effective_stage)
    was_planned = previous_stage in PLANNED_STAGES if previous_stage else False
    if current in SPRINT_SCOPE and commitment is None:
        sprint = await session.get(Sprint, workspace.active_sprint_id)
        session.add(
            SprintCommitment(
                sprint_id=workspace.active_sprint_id,
                card_id=card.id,
                effort_snapshot=card.effort_points,
                planned_count=await remaining_occurrences(session, card, sprint.planned_start_date, sprint.planned_end_date),
                scope_kind="added",
                added_at=utcnow(),
            )
        )
        if current in PLANNED_STAGES:
            record_change(session, SPRINT_JOINED, card.id)
    elif commitment and was_planned and current is CardStage.BACKLOG:
        commitment.removed_at = utcnow()
        record_change(session, SPRINT_LEFT, card.id)
    if commitment is None:
        return
    if previous_stage in TERMINAL_STAGES and current not in TERMINAL_STAGES:
        # A reopened Action is no longer a completed or cancelled Sprint result.
        commitment.result = None
    if commitment.removed_at is not None and current in PLANNED_STAGES:
        # Returning to Sprint scope cancels the earlier removal instead of
        # counting the same effort as both removed and selected.
        commitment.removed_at = None
        record_change(session, SPRINT_JOINED, card.id)


async def sync_successor_commitment(session: AsyncSession, previous: Card, successor: Card) -> None:
    """Transfer the reserved executions; opening a copy does not add the plan twice."""
    workspace = await require_workspace(session)
    if not workspace.active_sprint_id:
        return
    source = await session.scalar(select(SprintCommitment).where(
        SprintCommitment.sprint_id == workspace.active_sprint_id,
        SprintCommitment.card_id == previous.id,
    ))
    if source is None or source.removed_at is not None:
        await sync_commitment_for_stage(session, successor)
        return
    if CardStage(successor.effective_stage) not in PLANNED_STAGES:
        return
    remaining = max(0, source.planned_count - 1) if source.planned_count is not None else None
    source.planned_count = 1
    session.add(SprintCommitment(
        sprint_id=source.sprint_id, card_id=successor.id,
        effort_snapshot=source.effort_snapshot, planned_count=remaining,
        scope_kind=source.scope_kind, added_at=source.added_at, key_action=source.key_action,
    ))
    if source.key_action is None:
        record_change(session, SPRINT_JOINED, successor.id)


async def refresh_schedule_commitment(session: AsyncSession, card: Card) -> None:
    """Refresh the open copy's forecast; completed copies and their estimates stay fixed."""
    workspace = await require_workspace(session)
    if not workspace.active_sprint_id:
        return
    commitment = await session.scalar(select(SprintCommitment).where(
        SprintCommitment.sprint_id == workspace.active_sprint_id, SprintCommitment.card_id == card.id,
    ))
    if commitment is not None and commitment.result is None and commitment.removed_at is None:
        sprint = await session.get(Sprint, workspace.active_sprint_id)
        commitment.planned_count = await remaining_occurrences(session, card, sprint.planned_start_date, sprint.planned_end_date)


async def record_sprint_result(session: AsyncSession, card_id: int) -> None:
    """What the running Sprint says this Action came to."""
    workspace = await require_workspace(session)
    if not workspace.active_sprint_id:
        return
    commitment = await session.scalar(
        select(SprintCommitment).where(
            SprintCommitment.card_id == card_id,
            SprintCommitment.sprint_id == workspace.active_sprint_id,
        )
    )
    if commitment:
        if commitment.planned_count == 0:
            commitment.planned_count, commitment.scope_kind = 1, "added"
        commitment.result = CardStage.DONE.value


def effort_sums(commitments: Iterable[SprintCommitment]) -> dict[str, float]:
    """A Sprint's effort: committed at the start, added, removed, completed. The one place
    the four are added up, for the Sprint screen while it runs and for its record when it
    closes."""
    items = list(commitments)
    return {
        "committed": sum((i.effort_snapshot or 0) * i.quantity for i in items if i.scope_kind == "initial"),
        "added": sum((i.effort_snapshot or 0) * i.quantity for i in items if i.scope_kind == "added"),
        "removed": sum((i.effort_snapshot or 0) * i.removed_quantity for i in items),
        "completed": sum(i.effort_snapshot or 0 for i in items if i.result == CardStage.DONE.value),
    }


def action_counts(commitments: Iterable[SprintCommitment]) -> dict[str, int]:
    """Count Actions independently of whether they carry an estimate."""
    items = list(commitments)
    return {
        "committed": sum(i.quantity for i in items if i.scope_kind == "initial"),
        "added": sum(i.quantity for i in items if i.scope_kind == "added"),
        "removed": sum(i.removed_quantity for i in items),
        "completed": sum(i.result == CardStage.DONE.value for i in items),
        "unestimated": sum(i.quantity for i in items if i.effort_snapshot is None),
        "unknown_schedules": sum(i.planned_count is None for i in items),
    }


async def sprint_counts(session: AsyncSession, sprint_id: int) -> dict[str, int]:
    return action_counts(await session.scalars(
        select(SprintCommitment).where(SprintCommitment.sprint_id == sprint_id)
    ))


async def sprint_metrics(session: AsyncSession, sprint_id: int) -> dict[str, float]:
    """The Sprint's effort as it stands."""
    return effort_sums(
        await session.scalars(
            select(SprintCommitment).where(SprintCommitment.sprint_id == sprint_id)
        )
    )


async def delete_commitments_of_cards(session: AsyncSession, card_ids: list[int]) -> None:
    """Take deleted Cards out of every Sprint that ever counted them; one still open in the
    running Sprint leaves it unfinished, the way a move to Backlog does."""
    workspace = await session.get(Workspace, 1)
    if workspace is not None and workspace.active_sprint_id:
        leaving = await session.scalars(
            select(SprintCommitment.card_id).where(
                SprintCommitment.sprint_id == workspace.active_sprint_id,
                SprintCommitment.card_id.in_(card_ids),
                SprintCommitment.removed_at.is_(None),
                SprintCommitment.result.is_(None),
            )
        )
        for card_id in leaving:
            record_change(session, SPRINT_LEFT, card_id)
    await session.execute(delete(SprintCommitment).where(SprintCommitment.card_id.in_(card_ids)))


async def key_action_ids(session: AsyncSession) -> set[int]:
    """The Actions the running Sprint's Success criterion rests on, as marked."""
    workspace = await session.get(Workspace, 1)
    if workspace is None or not workspace.active_sprint_id:
        return set()
    return set(
        await session.scalars(
            select(SprintCommitment.card_id).where(
                SprintCommitment.sprint_id == workspace.active_sprint_id,
                SprintCommitment.key_action.is_(True),
            )
        )
    )


async def today_actions(session: AsyncSession, *, now: datetime | None = None) -> list[Card]:
    """The open Actions in Today, in the order the day cannot move.

    An appointment today or tomorrow first, then Critical, then the ones key to the Sprint,
    then the rest — each group in Today's own order: an appointment, then importance, then age.
    """
    cards = await actions_on_stages(session, CardStage.TODAY)
    keys = await key_action_ids(session)
    tz = ZoneInfo((await require_workspace(session)).timezone)
    today = (now or utcnow()).astimezone(tz).date()
    near = today + timedelta(days=SCHEDULE_NOTICE_DAYS)
    ranks = list(Priority)

    def cannot_move(card: Card) -> tuple[int, bool, float, int, datetime]:
        when = card.scheduled_at.astimezone(tz).date() if card.scheduled_at else None
        if when is not None and today <= when <= near:
            group = 0
        elif card.priority == Priority.CRITICAL.value:
            group = 1
        elif card.id in keys:
            group = 2
        else:
            group = 3
        return (
            group,
            card.scheduled_at is None,
            card.scheduled_at.timestamp() if card.scheduled_at else 0.0,
            ranks.index(Priority(card.priority)),
            card.created_at,
        )

    return sorted(cards, key=cannot_move)
