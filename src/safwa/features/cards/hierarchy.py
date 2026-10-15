"""What a parent Card shows, and the one walk that keeps it true.

A Goal and a Subgoal derive their live stage and archive from their children, and their
effort and time from the distinct Actions under them: a Card may stand under several Goals,
so one Action can be reached along two paths and still counts once. Done is explicit, held
in manual_stage, and nothing under a Goal sets or clears it. `propagate_ancestors` keeps the
plain columns current, and every path that changes an Action ends there. Blocked is an
Action's alone: a parent never derives it.

Reading a branch is here too, because the same walk answers what its Actions have completed.
"""


from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from .api import parents_of
from .model import (
    LIVE_STAGE_PRECEDENCE,
    TERMINAL_STAGES,
    Card,
    CardKind,
    CardParent,
    CardStage,
)


def aggregate_child_stages(children: list[Card]) -> CardStage:
    stages = [CardStage(child.effective_stage) for child in children]
    live = [stage for stage in stages if stage not in TERMINAL_STAGES]
    if live:
        return max(live, key=lambda stage: LIVE_STAGE_PRECEDENCE[stage])
    return CardStage.BACKLOG


async def branch_ids(session: AsyncSession, card_id: int) -> set[int]:
    """Every Card under this one, at any depth, each once."""
    down = (
        select(CardParent.card_id.label("id"))
        .where(CardParent.parent_id == card_id)
        .cte("down", recursive=True)
    )
    down = down.union(select(CardParent.card_id).join(down, CardParent.parent_id == down.c.id))
    return set(await session.scalars(select(down.c.id)))


async def branch_actions(session: AsyncSession, card_id: int) -> list[Card]:
    """Every Action in this Card's branch, each once, an archived one included.

    Archiving is a matter of sight, so an archived Action still counts in what its Goal
    shows: the effort it took and the Card it completed are still the owner's.
    """
    ids = await branch_ids(session, card_id)
    if not ids:
        return []
    return list(
        await session.scalars(
            select(Card)
            .where(Card.id.in_(ids), Card.kind == CardKind.ACTION.value)
            .order_by(Card.id)
        )
    )


async def ancestor_ids(session: AsyncSession, card_ids: Iterable[int]) -> list[int]:
    """These Cards and every Goal above them, each after every Card below it.

    A Card's place is its longest path up from the Cards named, so a child always comes
    before its parent, along every path.
    """
    start = sorted(set(card_ids))
    if not start:
        return []
    up = (
        select(Card.id.label("id"), literal(0).label("depth"))
        .where(Card.id.in_(start))
        .cte("up", recursive=True)
    )
    up = up.union(
        select(CardParent.parent_id, up.c.depth + 1).join(up, CardParent.card_id == up.c.id)
    )
    deepest = func.max(up.c.depth)
    return list(
        await session.scalars(select(up.c.id).group_by(up.c.id).order_by(deepest, up.c.id))
    )


async def levels_up(session: AsyncSession, card_ids: Iterable[int]) -> int:
    """How many Cards the longest path up from these takes, the one it starts at included."""
    start = sorted(set(card_ids))
    if not start:
        return 0
    up = (
        select(Card.id.label("id"), literal(1).label("level"))
        .where(Card.id.in_(start))
        .cte("up", recursive=True)
    )
    up = up.union(
        select(CardParent.parent_id, up.c.level + 1).join(up, CardParent.card_id == up.c.id)
    )
    return int(await session.scalar(select(func.max(up.c.level))) or 0)


async def levels_down(session: AsyncSession, card_id: int) -> int:
    """How many Cards the longest path down from this one takes, itself included."""
    down = (
        select(literal(card_id).label("id"), literal(1).label("level"))
        .cte("down", recursive=True)
    )
    down = down.union(
        select(CardParent.card_id, down.c.level + 1).join(
            down, CardParent.parent_id == down.c.id
        )
    )
    return int(await session.scalar(select(func.max(down.c.level))) or 1)


def derived_from_children(
    children: list[Card], actions: list[Card]
) -> tuple[CardStage, float | None, int | None, datetime | None]:
    """What a Goal or a Subgoal shows: the stage, the effort, the time and the archive.

    The stage and the archive come from the row below it, so a Subgoal with nothing in it
    still holds its Goal in Backlog. The effort and the time are summed over `actions`, the
    distinct Actions in the branch, so an Action under two of its Subgoals counts once.
    Finished children leave an open parent in Backlog; explicit completion is applied by
    `propagate_ancestors`.
    """
    if not children:
        return CardStage.BACKLOG, None, None, None
    effort = [action.effort_points for action in actions if action.effort_points is not None]
    tracked = [action.tracked_mins for action in actions if action.tracked_mins is not None]
    stamps = [child.archived_at for child in children]
    return (
        aggregate_child_stages(children),
        sum(effort) if effort else None,
        sum(tracked) if tracked else None,
        # A branch leaves sight when its last Card does, and one live Card brings it back.
        max(stamps) if all(stamp is not None for stamp in stamps) else None,
    )


async def propagate_ancestors(session: AsyncSession, start_ids: Iterable[int]) -> list[int]:
    """The one walk that writes a parent's derived values: stage, effort, time, archive.

    It rewrites the Goals named and every Goal above them, along every path, each after the
    Goals below it. The values are stored in the plain columns rather than computed on read,
    so Safwa reads one column that means the same thing on every row. The price is that
    every path which changes an Action has to end here.
    """
    changed: list[int] = []
    for card_id in await ancestor_ids(session, start_ids):
        parent = await session.get(Card, card_id)
        if parent is None or parent.kind == CardKind.ACTION.value:
            continue
        # An archived child still counts in what its parent shows: archiving is a matter
        # of sight, and the effort it took is still the owner's.
        children = await card_children(session, parent.id)
        actions = await branch_actions(session, parent.id)
        stage, effort, tracked, archived = derived_from_children(children, actions)
        if parent.manual_stage == CardStage.DONE.value:
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
    return changed


async def card_parent_ids(session: AsyncSession, card_id: int) -> list[int]:
    """The Goals this Card stands under."""
    return (await parents_of(session, [card_id])).get(card_id, [])


async def card_children(session: AsyncSession, card_id: int) -> list[Card]:
    """Every Card directly under this one. An archived child is shown marked, not left out."""
    return list(
        await session.scalars(
            select(Card)
            .join(CardParent, CardParent.card_id == Card.id)
            .where(CardParent.parent_id == card_id)
            .order_by(Card.id)
        )
    )


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

    The effort is summed over the distinct Actions among the Cards and under them, so an
    Action listed beside its Goal, or reached through two Goals, counts once.
    """
    actions = {card.id: card for card in cards if card.kind == CardKind.ACTION.value}
    goals = [card for card in cards if card.kind == CardKind.GOAL.value]
    for goal in goals:
        for action in await branch_actions(session, goal.id):
            actions.setdefault(action.id, action)
    branch = list(actions.values())
    effort = [action.effort_points for action in branch if action.effort_points is not None]
    return {
        "goals": len(goals),
        "actions": sum(card.kind == CardKind.ACTION.value for card in cards),
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

    The stamps are all written before the walk, so a parent reads its siblings as they will
    be rather than as they were halfway through.
    """
    changed = [action.id for action in actions]
    parents = await parents_of(session, changed)
    above = {parent for held in parents.values() for parent in held}
    for ancestor in await propagate_ancestors(session, above):
        if ancestor not in changed:
            changed.append(ancestor)
    return changed
