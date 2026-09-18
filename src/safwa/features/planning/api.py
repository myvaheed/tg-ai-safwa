"""What another feature may ask of Planning.

Cards calls the commitment operations after it has written a stage or deleted a Card: the
commitment rows are the Sprint's, and Cards never touches one itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.clock import utcnow

from ...foundation.workspace import Workspace, require_workspace
from ..cards.api import (
    HARD_TIME_NOTICE_DAYS,
    PLANNED_STAGES,
    TERMINAL_STAGES,
    Card,
    CardStage,
    actions_on_stages,
)
from ..cards.model import CardKind, Priority
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


async def active_sprint_end_date(session: AsyncSession) -> date | None:
    """The planned last day of the running Sprint, or None in Planning."""
    workspace = await session.get(Workspace, 1)
    if workspace is None or not workspace.active_sprint_id:
        return None
    sprint = await session.get(Sprint, workspace.active_sprint_id)
    return sprint.planned_end_date if sprint is not None else None


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
        session.add(
            SprintCommitment(
                sprint_id=workspace.active_sprint_id,
                card_id=card.id,
                effort_snapshot=card.effort_points or 0,
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
        commitment.result = CardStage.DONE.value


def effort_sums(commitments: Iterable[SprintCommitment]) -> dict[str, float]:
    """A Sprint's effort: committed at the start, added, removed, completed. The one place
    the four are added up, for the Sprint screen while it runs and for its record when it
    closes."""
    items = list(commitments)
    return {
        "committed": sum(i.effort_snapshot for i in items if i.scope_kind == "initial"),
        "added": sum(i.effort_snapshot for i in items if i.scope_kind == "added"),
        "removed": sum(i.effort_snapshot for i in items if i.removed_at is not None),
        "completed": sum(i.effort_snapshot for i in items if i.result == CardStage.DONE.value),
    }


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

    A Hard Time today or tomorrow first, then Critical, then the ones key to the Sprint,
    then the rest — each group in Today's own order: a Hard Time, then importance, then age.
    """
    cards = await actions_on_stages(session, CardStage.TODAY)
    keys = await key_action_ids(session)
    tz = ZoneInfo((await require_workspace(session)).timezone)
    today = (now or utcnow()).astimezone(tz).date()
    near = today + timedelta(days=HARD_TIME_NOTICE_DAYS)
    ranks = list(Priority)

    def cannot_move(card: Card) -> tuple[int, bool, float, int, datetime]:
        when = card.hard_time_at.astimezone(tz).date() if card.hard_time_at else None
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
            card.hard_time_at is None,
            card.hard_time_at.timestamp() if card.hard_time_at else 0.0,
            ranks.index(Priority(card.priority)),
            card.created_at,
        )

    return sorted(cards, key=cannot_move)
