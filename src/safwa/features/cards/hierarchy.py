"""What a parent Card shows, and the one walk that keeps it true.

A Goal and a Subgoal carry no stage, block, effort or archive of their own: each is added up
from the row of children below it and written into the plain columns, so every screen and
every view reads one column that means the same thing on every Card.  `propagate_ancestors`
is the only writer of those columns, and every path that changes an Action ends there.

Reading a branch is here too, because the same walk answers both: what a parent is blocked
for, and what its Actions have completed.
"""


from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .model import (
    LIVE_STAGE_PRECEDENCE,
    TERMINAL_STAGES,
    Card,
    CardKind,
    CardStage,
)


def aggregate_child_stages(children: list[Card]) -> CardStage:
    stages = [CardStage(child.effective_stage) for child in children]
    live = [stage for stage in stages if stage not in TERMINAL_STAGES]
    if live:
        return max(live, key=lambda stage: LIVE_STAGE_PRECEDENCE[stage])
    return CardStage.DONE


async def branch_actions(session: AsyncSession, card_id: int) -> list[Card]:
    """Every Action in this Card's branch, an archived one included.

    Archiving is a matter of sight, so an archived Action still counts in what its Goal
    shows: the effort it took and the Card it completed are still the owner's.
    """
    found: list[Card] = []
    for child in await session.scalars(select(Card).where(Card.parent_id == card_id)):
        if child.kind == CardKind.ACTION.value:
            found.append(child)
        else:
            found.extend(await branch_actions(session, child.id))
    return found


def derived_from_children(
    children: list[Card],
) -> tuple[CardStage, bool, int | None, datetime | None]:
    """What a Goal or a Subgoal shows: the stage, the block, the effort and the archive.

    Every child already carries its own derived values, so a parent adds up the row below
    it and the recursion reaches the Actions on its own. Reading the branch's Actions
    directly would skip a Subgoal with nothing in it, and a Goal would call itself Done over
    a child that never started.
    """
    if not children:
        return CardStage.BACKLOG, False, None, None
    effort = [child.effort_points for child in children if child.effort_points is not None]
    stamps = [child.archived_at for child in children]
    return (
        aggregate_child_stages(children),
        any(
            child.blocked and CardStage(child.effective_stage) not in TERMINAL_STAGES
            for child in children
        ),
        sum(effort) if effort else None,
        # A branch leaves sight when its last Card does, and one live Card brings it back.
        max(stamps) if all(stamp is not None for stamp in stamps) else None,
    )


async def propagate_ancestors(session: AsyncSession, start_parent_id: int | None) -> list[int]:
    """The one walk that writes a parent's derived values: stage, blocked, effort, archive.

    They are stored in the plain columns rather than computed on read, so Safwa reads one
    column that means the same thing on every row. The price is that every path which
    changes an Action has to end here.
    """
    changed: list[int] = []
    parent_id = start_parent_id
    while parent_id:
        parent = await session.get(Card, parent_id)
        if parent is None:
            break
        # An archived child still counts in what its parent shows: archiving is a matter
        # of sight, and the effort it took is still the owner's.
        children = list(await session.scalars(select(Card).where(Card.parent_id == parent.id)))
        stage, blocked, effort, archived = derived_from_children(children)
        current = (parent.effective_stage, parent.blocked, parent.effort_points, parent.archived_at)
        if current != (stage.value, blocked, effort, archived):
            parent.effective_stage = stage.value
            parent.blocked = blocked
            # Several blocked Actions have several reasons, and picking one would be
            # Safwa writing the owner's words. The screen quotes each Action instead.
            parent.blocked_description = ""
            parent.effort_points = effort
            parent.archived_at = archived
            parent.version += 1
            changed.append(parent.id)
        parent_id = parent.parent_id
    return changed


async def card_children(session: AsyncSession, card_id: int) -> list[Card]:
    """Every Card under this one. An archived child is shown marked, not left out."""
    return list(await session.scalars(select(Card).where(Card.parent_id == card_id)))


async def card_progress(session: AsyncSession, card_id: int) -> dict[str, int]:
    """Completed Action effort and direct-child completion for a Goal or a Subgoal.

    The branch total is not here: it is `effort_points` on the Card itself, written by
    `propagate_ancestors`, so a query reads the same number the screens do.
    """
    actions = await branch_actions(session, card_id)
    direct_children = await card_children(session, card_id)
    return {
        "completed_effort": sum(
            action.effort_points or 0
            for action in actions
            if action.effective_stage == CardStage.DONE.value
        ),
        "completed_children": sum(
            child.effective_stage == CardStage.DONE.value for child in direct_children
        ),
        "total_children": len(direct_children),
    }


async def blocking_actions(session: AsyncSession, card_id: int) -> list[Card]:
    """The Actions a parent reads as blocked for, each with the reason it gave."""
    return [
        action
        for action in await branch_actions(session, card_id)
        if action.blocked and CardStage(action.effective_stage) not in TERMINAL_STAGES
    ]


async def settle_archive(session: AsyncSession, actions: list[Card]) -> list[int]:
    """Every Card the archive moved: the Actions that were stamped, then their branches.

    The stamps are all written before the first walk, so a parent reads its siblings as
    they will be rather than as they were halfway through.
    """
    changed = [action.id for action in actions]
    for action in actions:
        for ancestor in await propagate_ancestors(session, action.parent_id):
            if ancestor not in changed:
                changed.append(ancestor)
    return changed
