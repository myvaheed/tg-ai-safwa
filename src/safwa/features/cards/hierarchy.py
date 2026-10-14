"""What a parent Card shows, and the one walk that keeps it true.

A Goal and a Subgoal derive their live stage, effort, time and archive from their
children. Done is explicit and held in manual_stage. `propagate_ancestors` keeps the plain
columns current, and every path that changes an Action ends there. Blocked is an Action's
alone: a parent never derives it.

Reading a branch is here too, because the same walk answers what its Actions have completed.
"""


from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..checks.use_cases import reopen_checks
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
    return CardStage.BACKLOG


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
) -> tuple[CardStage, float | None, int | None, datetime | None]:
    """What a Goal or a Subgoal shows: the stage, the effort, the time and the archive.

    Every child already carries its own derived values, so a parent adds up the row below
    it and the recursion reaches the Actions on its own. Reading the branch's Actions
    directly would skip a Subgoal with nothing in it. Finished children leave an open
    parent in Backlog; explicit completion is applied by `propagate_ancestors`.
    """
    if not children:
        return CardStage.BACKLOG, None, None, None
    effort = [child.effort_points for child in children if child.effort_points is not None]
    tracked = [child.tracked_mins for child in children if child.tracked_mins is not None]
    stamps = [child.archived_at for child in children]
    return (
        aggregate_child_stages(children),
        sum(effort) if effort else None,
        sum(tracked) if tracked else None,
        # A branch leaves sight when its last Card does, and one live Card brings it back.
        max(stamps) if all(stamp is not None for stamp in stamps) else None,
    )


async def propagate_ancestors(session: AsyncSession, start_parent_id: int | None) -> list[int]:
    """The one walk that writes a parent's derived values: stage, effort, time, archive.

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
        stage, effort, tracked, archived = derived_from_children(children)
        if parent.manual_stage == CardStage.DONE.value:
            actions = await branch_actions(session, parent.id)
            if any(action.effective_stage != CardStage.DONE.value for action in actions):
                parent.manual_stage = CardStage.BACKLOG.value
                parent.completed_at = None
                archived = None
                await reopen_checks(session, parent.id)
            else:
                stage = CardStage.DONE
        else:
            archived = None
        current = (
            parent.effective_stage,
            parent.effort_points,
            parent.tracked_mins,
            parent.archived_at,
        )
        if current != (stage.value, effort, tracked, archived):
            parent.effective_stage = stage.value
            parent.effort_points = effort
            parent.tracked_mins = tracked
            parent.archived_at = archived
            parent.version += 1
            changed.append(parent.id)
        parent_id = parent.parent_id
    return changed


async def card_children(session: AsyncSession, card_id: int) -> list[Card]:
    """Every Card under this one. An archived child is shown marked, not left out."""
    return list(await session.scalars(select(Card).where(Card.parent_id == card_id)))


async def card_progress(session: AsyncSession, card_id: int) -> dict[str, Any]:
    """What a Goal or a Subgoal adds up to: its children counted by `cards_progress`, and
    how many of them are Done."""
    direct_children = await card_children(session, card_id)
    return {
        **await cards_progress(session, direct_children),
        "completed_children": sum(
            child.effective_stage == CardStage.DONE.value for child in direct_children
        ),
        "total_children": len(direct_children),
    }


async def cards_progress(session: AsyncSession, cards: list[Card]) -> dict[str, Any]:
    """Goals and Actions among `cards`, and the effort their branches took of what they hold.

    Counted as a Goal counts its children: the total is the Cards' own `effort_points`, a
    Goal's being the sum `propagate_ancestors` wrote, and Done effort is the Actions' below.
    """
    actions = [card for card in cards if card.kind == CardKind.ACTION.value]
    goals = [card for card in cards if card.kind == CardKind.GOAL.value]
    branch = actions + [action for goal in goals for action in await branch_actions(session, goal.id)]
    effort = [card.effort_points for card in cards if card.effort_points is not None]
    return {
        "goals": len(goals),
        "actions": len(actions),
        "effort_points": sum(effort) if effort else None,
        "completed_effort": sum(
            action.effort_points or 0
            for action in branch
            if action.effective_stage == CardStage.DONE.value
        ),
        "unestimated_actions": sum(action.effort_points is None for action in branch),
    }


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
