"""What another feature may ask of Planning.

Cards calls every one of these after it has written a stage or deleted a Card: the
commitment rows are the Sprint's, and Cards never touches one itself. The archive cutoff
is here for the same reason — how long a closed Card has waited is counted in Sprints.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...constants import ARCHIVE_AFTER_SPRINTS
from ...enums import CardKind
from ...foundation.clock import utcnow
from ...foundation.workspace import require_workspace
from ..cards.api import TERMINAL_STAGES, Card, CardStage
from .model import Sprint, SprintCommitment

# The stages an Action has to be on for a Sprint to have anything to say about it.
SPRINT_SCOPE = frozenset({CardStage.SPRINT, CardStage.TODAY, CardStage.DONE, CardStage.CANCELLED})
PLANNED_STAGES = frozenset({CardStage.SPRINT, CardStage.TODAY})


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
    elif commitment and was_planned and current is CardStage.BACKLOG:
        commitment.removed_at = utcnow()
    if commitment is None:
        return
    if previous_stage in TERMINAL_STAGES and current not in TERMINAL_STAGES:
        # A reopened Action is no longer a completed or cancelled Sprint result.
        commitment.result = None
    if commitment.removed_at is not None and current in PLANNED_STAGES:
        # Returning to Sprint scope cancels the earlier removal instead of
        # counting the same effort as both removed and selected.
        commitment.removed_at = None


async def record_sprint_result(
    session: AsyncSession, card_id: int, terminal_stage: CardStage
) -> None:
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
        commitment.result = terminal_stage.value


async def delete_commitments_of_cards(session: AsyncSession, card_ids: list[int]) -> None:
    """Take deleted Cards out of every Sprint that ever counted them."""
    await session.execute(delete(SprintCommitment).where(SprintCommitment.card_id.in_(card_ids)))


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
