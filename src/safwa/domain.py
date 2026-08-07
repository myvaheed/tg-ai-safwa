from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import (
    EFFORT_POINTS,
    LIVE_STAGE_PRECEDENCE,
    TERMINAL_STAGES,
    ActorType,
    CardKind,
    CardStage,
    WorkspaceMode,
)
from .models import (
    Board,
    Card,
    CardCategory,
    CardDependency,
    CardEnergyType,
    CardEvent,
    CardValue,
    FeedbackQueue,
    Sprint,
    SprintCommitment,
    UserProfile,
    Workspace,
    new_id,
)


class DomainError(ValueError):
    pass


class StaleStateError(DomainError):
    pass


@dataclass
class OperationResult:
    card_ids: list[str] = field(default_factory=list)
    ancestor_ids: list[str] = field(default_factory=list)
    successor_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def utcnow() -> datetime:
    return datetime.now(UTC)


def card_snapshot(card: Card) -> dict[str, Any]:
    return {
        "id": card.id,
        "board_id": card.board_id,
        "parent_id": card.parent_id,
        "kind": card.kind,
        "title": card.title,
        "stage": card.effective_stage,
        "priority": card.priority,
        "effort_points": card.effort_points,
        "repeatable": card.repeatable,
        "version": card.version,
    }


async def bootstrap_workspace(session: AsyncSession, owner_id: int, timezone: str) -> Workspace:
    workspace = await session.get(Workspace, 1)
    if workspace is None:
        workspace = Workspace(id=1, owner_telegram_id=owner_id, timezone=timezone)
        session.add(workspace)
    elif workspace.owner_telegram_id != owner_id:
        raise DomainError("The database is already bound to another Telegram owner")
    profile = await session.get(UserProfile, 1)
    if profile is None:
        session.add(UserProfile(id=1))
    inbox = await session.scalar(select(Board).where(Board.name == "Inbox"))
    if inbox is None:
        session.add(Board(name="Inbox", description="Default board"))
    await session.flush()
    return workspace


async def _workspace(session: AsyncSession) -> Workspace:
    workspace = await session.get(Workspace, 1)
    if workspace is None:
        raise DomainError("Workspace is not initialized")
    return workspace


async def _bump_workspace(session: AsyncSession) -> Workspace:
    workspace = await _workspace(session)
    workspace.revision += 1
    return workspace


async def validate_parent(
    session: AsyncSession,
    kind: CardKind | str,
    board_id: str,
    parent_id: str | None,
    *,
    card_id: str | None = None,
) -> Card | None:
    kind = CardKind(kind)
    if parent_id is None:
        return None
    if kind is CardKind.GOAL:
        raise DomainError("A Goal must be root-level")
    parent = await session.get(Card, parent_id)
    if parent is None or parent.archived_at is not None:
        raise DomainError("Parent does not exist or is archived")
    if parent.board_id != board_id:
        raise DomainError("Parent and child must be on the same Board")
    if parent.kind == CardKind.ACTION.value:
        raise DomainError("An Action cannot have children")
    if kind is CardKind.IDEA and parent.kind != CardKind.GOAL.value:
        raise DomainError("An Idea may only be placed under a Goal")
    if card_id:
        cursor: Card | None = parent
        while cursor is not None:
            if cursor.id == card_id:
                raise DomainError("Card hierarchy cannot contain a cycle")
            cursor = await session.get(Card, cursor.parent_id) if cursor.parent_id else None
    return parent


def validate_action_fields(
    kind: CardKind | str,
    effort_points: int | None,
    repeatable: bool,
    categories: set[str] | None = None,
    energy_types: set[str] | None = None,
) -> None:
    kind = CardKind(kind)
    if kind is CardKind.ACTION:
        if effort_points not in EFFORT_POINTS:
            raise DomainError("An Action needs effort points: 1, 2, 3, 5, 8, or 13")
        return
    if effort_points is not None or repeatable or categories or energy_types:
        raise DomainError("Goal and Idea cards cannot have Action-only fields")


async def _children(session: AsyncSession, card_id: str) -> list[Card]:
    return list(
        await session.scalars(
            select(Card).where(Card.parent_id == card_id, Card.archived_at.is_(None))
        )
    )


async def effective_value_ids(session: AsyncSession, card_id: str) -> set[str]:
    """Direct Values plus descendant Values, without duplicating stored links."""
    pending = [card_id]
    card_ids: list[str] = []
    while pending:
        current = pending.pop()
        card_ids.append(current)
        pending.extend(child.id for child in await _children(session, current))
    return set(
        await session.scalars(select(CardValue.value_id).where(CardValue.card_id.in_(card_ids)))
    )


async def unresolved_blockers(session: AsyncSession, card_id: str) -> list[Card]:
    blocker_ids = list(
        await session.scalars(
            select(CardDependency.blocker_card_id).where(CardDependency.blocked_card_id == card_id)
        )
    )
    if not blocker_ids:
        return []
    return list(
        await session.scalars(
            select(Card).where(
                Card.id.in_(blocker_ids), Card.effective_stage != CardStage.DONE.value
            )
        )
    )


async def ensure_dependency_acyclic(
    session: AsyncSession, blocked_card_id: str, blocker_card_id: str
) -> None:
    if blocked_card_id == blocker_card_id:
        raise DomainError("A Card cannot block itself")
    pending = [blocker_card_id]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == blocked_card_id:
            raise DomainError("Card dependencies cannot contain a cycle")
        if current in seen:
            continue
        seen.add(current)
        pending.extend(
            await session.scalars(
                select(CardDependency.blocker_card_id).where(
                    CardDependency.blocked_card_id == current
                )
            )
        )


def aggregate_child_stages(children: list[Card]) -> CardStage:
    stages = [CardStage(child.effective_stage) for child in children]
    live = [stage for stage in stages if stage not in TERMINAL_STAGES]
    if live:
        return max(live, key=lambda stage: LIVE_STAGE_PRECEDENCE[stage])
    if stages and all(stage is CardStage.CANCELLED for stage in stages):
        return CardStage.CANCELLED
    return CardStage.DONE


async def propagate_ancestors(session: AsyncSession, start_parent_id: str | None) -> list[str]:
    changed: list[str] = []
    parent_id = start_parent_id
    while parent_id:
        parent = await session.get(Card, parent_id)
        if parent is None:
            break
        children = await _children(session, parent.id)
        next_stage = (
            aggregate_child_stages(children) if children else CardStage(parent.manual_stage)
        )
        if parent.effective_stage != next_stage.value:
            parent.effective_stage = next_stage.value
            parent.version += 1
            changed.append(parent.id)
        parent_id = parent.parent_id
    return changed


async def _record_event(
    session: AsyncSession,
    card: Card,
    operation: str,
    actor: ActorType,
    before: dict[str, Any] | None,
    correlation_id: str,
) -> None:
    workspace = await _workspace(session)
    session.add(
        CardEvent(
            card_id=card.id,
            sprint_id=workspace.active_sprint_id,
            actor=actor.value,
            operation=operation,
            before=before,
            after=card_snapshot(card),
            correlation_id=correlation_id,
        )
    )


async def _sync_commitment_for_stage(
    session: AsyncSession, card: Card, previous_stage: CardStage | None = None
) -> None:
    workspace = await _workspace(session)
    if not workspace.active_sprint_id or card.kind != CardKind.ACTION.value:
        return
    commitment = await session.scalar(
        select(SprintCommitment).where(
            SprintCommitment.sprint_id == workspace.active_sprint_id,
            SprintCommitment.card_id == card.id,
        )
    )
    current = CardStage(card.effective_stage)
    in_scope = current in {CardStage.SPRINT, CardStage.TODAY, CardStage.DONE, CardStage.CANCELLED}
    was_scope = previous_stage in {CardStage.SPRINT, CardStage.TODAY} if previous_stage else False
    if in_scope and commitment is None:
        session.add(
            SprintCommitment(
                sprint_id=workspace.active_sprint_id,
                card_id=card.id,
                effort_snapshot=card.effort_points or 0,
                scope_kind="added",
                added_at=utcnow(),
            )
        )
    elif commitment and was_scope and current is CardStage.BACKLOG:
        commitment.removed_at = utcnow()


async def move_card(
    session: AsyncSession,
    card_id: str,
    stage: CardStage,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> OperationResult:
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist")
    if (
        stage in TERMINAL_STAGES
        and card.kind != CardKind.ACTION.value
        and await _children(session, card.id)
    ):
        raise DomainError("A populated Goal or Idea completes through its children")
    correlation_id = new_id()
    result = OperationResult(card_ids=[card.id])
    blockers = await unresolved_blockers(session, card.id)
    result.warnings.extend(f"Blocked by {blocker.title}" for blocker in blockers)

    async def move_subtree(node: Card) -> None:
        before = card_snapshot(node)
        previous = CardStage(node.effective_stage)
        if previous in TERMINAL_STAGES and stage not in TERMINAL_STAGES:
            node.completed_at = None
            node.cancelled_at = None
            node.liked = None
            await session.execute(delete(FeedbackQueue).where(FeedbackQueue.card_id == node.id))
        node.manual_stage = stage.value
        node.effective_stage = stage.value
        node.version += 1
        await _record_event(session, node, "move", actor, before, correlation_id)
        await _sync_commitment_for_stage(session, node, previous)
        for child in await _children(session, node.id):
            await move_subtree(child)

    await move_subtree(card)
    result.ancestor_ids = await propagate_ancestors(session, card.parent_id)
    await _bump_workspace(session)
    return result


async def _copy_repeat_successor(session: AsyncSession, card: Card, live_stage: CardStage) -> Card:
    series_id = card.repeat_series_id or new_id()
    card.repeat_series_id = series_id
    successor = Card(
        board_id=card.board_id,
        parent_id=card.parent_id,
        kind=card.kind,
        title=card.title,
        note=card.note,
        manual_stage=live_stage.value,
        effective_stage=live_stage.value,
        priority=card.priority,
        hard_time=card.hard_time,
        effort_points=card.effort_points,
        repeatable=True,
        repeat_series_id=series_id,
        source_instance_id=card.id,
    )
    session.add(successor)
    await session.flush()
    for link in await session.scalars(select(CardValue).where(CardValue.card_id == card.id)):
        session.add(CardValue(card_id=successor.id, value_id=link.value_id))
    for link in await session.scalars(select(CardCategory).where(CardCategory.card_id == card.id)):
        session.add(CardCategory(card_id=successor.id, category=link.category))
    for link in await session.scalars(
        select(CardEnergyType).where(CardEnergyType.card_id == card.id)
    ):
        session.add(CardEnergyType(card_id=successor.id, energy_type=link.energy_type))
    dependencies = await session.scalars(
        select(CardDependency).where(
            CardDependency.blocked_card_id == card.id,
            CardDependency.copy_to_repeat.is_(True),
        )
    )
    for dependency in dependencies:
        session.add(
            CardDependency(
                blocked_card_id=successor.id,
                blocker_card_id=dependency.blocker_card_id,
                copy_to_repeat=True,
            )
        )
    await _sync_commitment_for_stage(session, successor)
    return successor


async def finish_action(
    session: AsyncSession,
    card_id: str,
    terminal_stage: CardStage,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> OperationResult:
    if terminal_stage not in TERMINAL_STAGES:
        raise DomainError("Finish stage must be Done or Cancelled")
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist")
    if card.kind != CardKind.ACTION.value:
        raise DomainError("Only Actions are finished directly")
    if CardStage(card.effective_stage) in TERMINAL_STAGES:
        raise DomainError("Action is already terminal")
    previous_live_stage = CardStage(card.effective_stage)
    before = card_snapshot(card)
    now = utcnow()
    card.manual_stage = terminal_stage.value
    card.effective_stage = terminal_stage.value
    card.completed_at = now if terminal_stage is CardStage.DONE else None
    card.cancelled_at = now if terminal_stage is CardStage.CANCELLED else None
    card.version += 1
    correlation_id = new_id()
    await _record_event(session, card, terminal_stage.value, actor, before, correlation_id)
    workspace = await _workspace(session)
    commitment = (
        await session.scalar(
            select(SprintCommitment).where(
                SprintCommitment.card_id == card.id,
                SprintCommitment.sprint_id == workspace.active_sprint_id,
            )
        )
        if workspace.active_sprint_id
        else None
    )
    if commitment:
        commitment.result = terminal_stage.value
    result = OperationResult(card_ids=[card.id])
    blockers = await unresolved_blockers(session, card.id)
    result.warnings.extend(f"Blocked by {blocker.title}" for blocker in blockers)
    if terminal_stage is CardStage.DONE:
        session.add(FeedbackQueue(card_id=card.id))
    if card.repeatable:
        successor = await _copy_repeat_successor(session, card, previous_live_stage)
        result.successor_ids.append(successor.id)
    result.ancestor_ids = await propagate_ancestors(session, card.parent_id)
    await _bump_workspace(session)
    return result


async def set_feedback(session: AsyncSession, queue_id: str, liked: bool) -> Card:
    item = await session.get(FeedbackQueue, queue_id)
    if item is None:
        raise DomainError("Feedback request no longer exists")
    if item.answered_at is not None:
        card = await session.get(Card, item.card_id)
        if card is None:
            raise DomainError("Card no longer exists")
        return card
    card = await session.get(Card, item.card_id)
    if card is None:
        raise DomainError("Card no longer exists")
    card.liked = liked
    card.version += 1
    item.answer = liked
    item.answered_at = utcnow()
    await _bump_workspace(session)
    return card


async def start_sprint(
    session: AsyncSession,
    *,
    start_date: date | None = None,
    capacity: int | None = None,
) -> Sprint:
    workspace = await _workspace(session)
    if WorkspaceMode(workspace.mode) is not WorkspaceMode.PLANNING or workspace.active_sprint_id:
        raise DomainError("A Sprint can start only from Planning")
    count = await session.scalar(select(func.count(Sprint.id))) or 0
    start = start_date or date.today()
    sprint = Sprint(
        number=count + 1,
        planned_start_date=start,
        planned_end_date=start + timedelta(days=13),
        actual_started_at=utcnow(),
        capacity_effort_points=capacity,
    )
    session.add(sprint)
    await session.flush()
    cards = await session.scalars(
        select(Card).where(
            Card.kind == CardKind.ACTION.value,
            Card.archived_at.is_(None),
            Card.effective_stage.in_([CardStage.SPRINT.value, CardStage.TODAY.value]),
        )
    )
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


async def finish_sprint(session: AsyncSession, *, reason: str = "finished") -> Sprint:
    workspace = await _workspace(session)
    if not workspace.active_sprint_id:
        raise DomainError("No Sprint is active")
    sprint = await session.get(Sprint, workspace.active_sprint_id)
    if sprint is None:
        raise DomainError("Active Sprint is missing")
    sprint.status = "finished"
    sprint.finish_reason = reason
    sprint.actual_ended_at = utcnow()
    workspace.mode = WorkspaceMode.PLANNING.value
    workspace.active_sprint_id = None
    workspace.revision += 1
    return sprint


async def sprint_metrics(session: AsyncSession, sprint_id: str) -> dict[str, int]:
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


async def archive_subtree(session: AsyncSession, card_id: str, archive: bool = True) -> list[str]:
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    changed: list[str] = []
    stamp = utcnow() if archive else None

    async def visit(node: Card) -> None:
        node.archived_at = stamp
        node.version += 1
        changed.append(node.id)
        for child in await session.scalars(select(Card).where(Card.parent_id == node.id)):
            await visit(child)

    await visit(card)
    await propagate_ancestors(session, card.parent_id)
    await _bump_workspace(session)
    return changed


async def delete_subtree(session: AsyncSession, card_id: str) -> int:
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    parent_id = card.parent_id
    ids: list[str] = []

    async def collect(node: Card) -> None:
        ids.append(node.id)
        for child in await session.scalars(select(Card).where(Card.parent_id == node.id)):
            await collect(child)

    await collect(card)
    await session.execute(delete(Card).where(Card.id.in_(ids)))
    await propagate_ancestors(session, parent_id)
    await _bump_workspace(session)
    return len(ids)
