"""Writing a Card: what one is, where it may sit, and the fields its kind may carry.

A Sprint commitment follows an Action's stage, and every writer of that stage is in this
file: each one calls Planning's door afterwards, and no Card row here ever touches a
commitment itself.

What a Goal or a Subgoal then shows is not written here. Each operation ends at
`propagate_ancestors` in [hierarchy.py](hierarchy.py), which is the only writer of the
derived columns and the only walk that reads a branch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError

from ...enums import ActorType
from ...foundation.workspace import bump_workspace, require_workspace
from ..checks.model import Check
from ..checks.use_cases import (
    check_card_id,
    clone_checks_for_successor,
    delete_checks_of_cards,
    reopen_checks,
    require_check_answers,
    settle_checks,
)
from ..planning.api import (
    delete_commitments_of_cards,
    record_sprint_result,
    sync_commitment_for_stage,
)
from ..tags.api import Tag, attach_tags, unlinkable_tag_id
from ..tags.model import CardTag
from ..values.api import Value, attach_values, unlinkable_value_id
from ..values.model import CardValue
from .hierarchy import branch_actions, card_children, propagate_ancestors, settle_archive
from .model import (
    EFFORT_POINTS,
    TERMINAL_STAGES,
    Card,
    CardCategory,
    CardCheck,
    CardEnergyType,
    CardEvent,
    CardKind,
    CardStage,
    Category,
    EnergyType,
    Priority,
    effort_label,
    new_correlation_id,
)

# The fields an Action alone carries. On a Goal and a Subgoal two of them are derived, so
# nothing outside `propagate_ancestors` may write one.
ACTION_ONLY_FIELDS = ("effort_points", "repeatable", "blocked", "blocked_description")


@dataclass
class OperationResult:
    card_ids: list[int] = field(default_factory=list)
    ancestor_ids: list[int] = field(default_factory=list)
    successor_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def card_snapshot(card: Card) -> dict[str, Any]:
    return {
        "id": card.id,
        "parent_id": card.parent_id,
        "kind": card.kind,
        "title": card.title,
        "stage": card.effective_stage,
        "priority": card.priority,
        "blocked": card.blocked,
        "blocked_description": card.blocked_description,
        "effort_points": card.effort_points,
        "repeatable": card.repeatable,
        "version": card.version,
    }


async def create_card(
    session: AsyncSession,
    *,
    kind: CardKind | str,
    title: str,
    note: str = "",
    stage: CardStage | str = CardStage.BACKLOG,
    priority: Priority | str = Priority.MEDIUM,
    hard_time: bool = False,
    blocked: bool = False,
    blocked_description: str = "",
    effort_points: float | None = None,
    repeatable: bool = False,
    parent_id: int | None = None,
    categories: set[Category | str] | None = None,
    energy_types: set[EnergyType | str] | None = None,
    value_ids: set[int] | None = None,
    tag_ids: set[int] | None = None,
    check_ids: set[int] | None = None,
    actor: ActorType = ActorType.USER_UI,
) -> Card:
    """Create one reviewed Card through the same domain boundary used by UI and AI."""
    card_kind = CardKind(kind)
    card_stage = CardStage(stage)
    card_priority = Priority(priority)
    clean_title = title.strip()
    clean_description = blocked_description.strip()
    if not clean_title:
        raise DomainError("Card title cannot be empty")
    if card_stage in TERMINAL_STAGES:
        raise DomainError("A new Card must start in Backlog, Sprint, or Today")
    category_values = {Category(item).value for item in (categories or set())}
    energy_values = {EnergyType(item).value for item in (energy_types or set())}
    if card_kind is not CardKind.ACTION:
        # A stage belongs to an Action, like effort and Blocked: a parent shows what its
        # branch is in, so anything asked for here is dropped rather than refused.
        card_stage = CardStage.BACKLOG
        effort_points = None
        repeatable = False
        blocked = False
        clean_description = ""
        category_values.clear()
        energy_values.clear()
    validate_action_fields(
        card_kind,
        effort_points,
        repeatable,
        category_values,
        energy_values,
        blocked=blocked,
    )
    validate_blocked_fields(blocked, clean_description)
    await validate_parent(session, card_kind, parent_id)

    if (loose := await unlinkable_value_id(session, value_ids or set())) is not None:
        raise DomainError(f"Value #{loose} does not exist")
    if (loose := await unlinkable_tag_id(session, tag_ids or set())) is not None:
        raise DomainError(f"Tag #{loose} does not exist")
    for check_id in check_ids or set():
        check = await session.get(Check, check_id)
        if check is None or check.archived_at is not None:
            raise DomainError(f"Check #{check_id} does not exist or is archived")
        if (held_by := await check_card_id(session, check_id)) is not None:
            raise DomainError(f"Check #{check_id} already belongs to Card #{held_by}")

    card = Card(
        parent_id=parent_id,
        kind=card_kind.value,
        title=clean_title,
        note=note.strip(),
        manual_stage=card_stage.value,
        effective_stage=card_stage.value,
        priority=card_priority.value,
        hard_time=hard_time,
        blocked=blocked,
        blocked_description=clean_description if blocked else "",
        effort_points=effort_points,
        repeatable=repeatable,
    )
    session.add(card)
    await session.flush()
    for category in sorted(category_values):
        session.add(CardCategory(card_id=card.id, category=category))
    for energy_type in sorted(energy_values):
        session.add(CardEnergyType(card_id=card.id, energy_type=energy_type))
    await attach_values(session, card.id, value_ids or set())
    await attach_tags(session, card.id, tag_ids or set())
    for check_id in sorted(check_ids or set()):
        session.add(CardCheck(card_id=card.id, check_id=check_id))
    await record_card_event(session, card, "create", actor, None, new_correlation_id())
    if card_kind is CardKind.ACTION:
        await sync_commitment_for_stage(session, card)
    await propagate_ancestors(session, parent_id)
    await bump_workspace(session)
    return card


async def edit_card_text(session: AsyncSession, card_id: int, field: str, value: str) -> Card:
    if field not in {"title", "note", "blocked_description"}:
        raise DomainError("Only a Card title, Note, or blocked description can be edited as text")
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    normalized = value.strip()
    if field == "title" and not normalized:
        raise DomainError("Card title cannot be empty")
    before = card_snapshot(card)
    setattr(card, field, normalized)
    if not card.blocked:
        card.blocked_description = ""
    validate_blocked_fields(card.blocked, card.blocked_description)
    card.version += 1
    await record_card_event(
        session, card, f"edit_{field}", ActorType.USER_UI, before, new_correlation_id()
    )
    await bump_workspace(session)
    return card


async def update_card_fields(
    session: AsyncSession,
    card_id: int,
    fields: dict[str, Any],
    *,
    actor: ActorType = ActorType.USER_UI,
) -> Card:
    """Apply validated editable Card fields through the domain/audit boundary."""
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    allowed = {
        "title",
        "note",
        "priority",
        "hard_time",
        "blocked",
        "blocked_description",
        "effort_points",
        "repeatable",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise DomainError("Unsupported Card fields: " + ", ".join(sorted(unknown)))
    if card.kind != CardKind.ACTION.value:
        # `blocked` and `effort_points` on a parent are derived values this walk writes;
        # a caller that set one by hand would be overwritten at the next Action change.
        for name in ACTION_ONLY_FIELDS:
            fields.pop(name, None)
        if not fields:
            raise DomainError("Goal and Subgoal cards cannot have Action-only fields")
    before = card_snapshot(card)
    for name, value in fields.items():
        if name in {"title", "note"}:
            value = str(value).strip()
        if name == "title" and not value:
            raise DomainError("Card title cannot be empty")
        if name == "priority":
            value = Priority(value).value
        setattr(card, name, value)
    if card.kind == CardKind.ACTION.value:
        validate_action_fields(card.kind, card.effort_points, card.repeatable, blocked=card.blocked)
        if not card.blocked:
            card.blocked_description = ""
        validate_blocked_fields(card.blocked, card.blocked_description)
    card.version += 1
    await record_card_event(session, card, "update", actor, before, new_correlation_id())
    await propagate_ancestors(session, card.parent_id)
    await bump_workspace(session)
    return card


async def toggle_card_value(
    session: AsyncSession, card_id: int, value_id: int, *, actor: ActorType = ActorType.USER_UI
) -> bool:
    """Toggle a direct Value link and return whether it is now linked."""
    card = await session.get(Card, card_id)
    value = await session.get(Value, value_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    if value is None:
        raise DomainError("Value does not exist")
    link = await session.scalar(
        select(CardValue).where(CardValue.card_id == card_id, CardValue.value_id == value_id)
    )
    before = card_snapshot(card)
    if link is None:
        session.add(CardValue(card_id=card_id, value_id=value_id))
        operation, linked = "link_value", True
    else:
        await session.delete(link)
        operation, linked = "unlink_value", False
    card.version += 1
    await record_card_event(session, card, operation, actor, before, new_correlation_id())
    await bump_workspace(session)
    return linked


async def toggle_card_tag(
    session: AsyncSession, card_id: int, tag_id: int, *, actor: ActorType = ActorType.USER_UI
) -> bool:
    """Toggle a direct Tag link and return whether it is now linked."""
    card = await session.get(Card, card_id)
    tag = await session.get(Tag, tag_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    if tag is None:
        raise DomainError("Tag does not exist")
    link = await session.scalar(
        select(CardTag).where(CardTag.card_id == card_id, CardTag.tag_id == tag_id)
    )
    before = card_snapshot(card)
    if link is None:
        session.add(CardTag(card_id=card_id, tag_id=tag_id))
        operation, linked = "link_tag", True
    else:
        await session.delete(link)
        operation, linked = "unlink_tag", False
    card.version += 1
    await record_card_event(session, card, operation, actor, before, new_correlation_id())
    await bump_workspace(session)
    return linked


async def toggle_card_category(
    session: AsyncSession,
    card_id: int,
    category: Category,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> bool:
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    if card.kind != CardKind.ACTION.value:
        raise DomainError("Only Actions can have Categories")
    link = await session.scalar(
        select(CardCategory).where(
            CardCategory.card_id == card_id,
            CardCategory.category == category.value,
        )
    )
    before = card_snapshot(card)
    if link is None:
        session.add(CardCategory(card_id=card_id, category=category.value))
        operation, linked = "link_category", True
    else:
        await session.delete(link)
        operation, linked = "unlink_category", False
    card.version += 1
    await record_card_event(session, card, operation, actor, before, new_correlation_id())
    await bump_workspace(session)
    return linked


async def toggle_card_energy_type(
    session: AsyncSession,
    card_id: int,
    energy_type: EnergyType,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> bool:
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    if card.kind != CardKind.ACTION.value:
        raise DomainError("Only Actions can have Energy types")
    link = await session.scalar(
        select(CardEnergyType).where(
            CardEnergyType.card_id == card_id,
            CardEnergyType.energy_type == energy_type.value,
        )
    )
    before = card_snapshot(card)
    if link is None:
        session.add(CardEnergyType(card_id=card_id, energy_type=energy_type.value))
        operation, linked = "link_energy", True
    else:
        await session.delete(link)
        operation, linked = "unlink_energy", False
    card.version += 1
    await record_card_event(session, card, operation, actor, before, new_correlation_id())
    await bump_workspace(session)
    return linked


async def toggle_card_check(
    session: AsyncSession, card_id: int, check_id: int, *, actor: ActorType = ActorType.USER_UI
) -> bool:
    """Toggle a direct Check link and return whether it is now linked."""
    card = await session.get(Card, card_id)
    check = await session.get(Check, check_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    link = await session.get(CardCheck, (card_id, check_id))
    if link is None and (held_by := await check_card_id(session, check_id)) is not None:
        raise DomainError(f"Check #{check_id} already belongs to Card #{held_by}")
    before = card_snapshot(card)
    if link is None:
        session.add(CardCheck(card_id=card_id, check_id=check_id))
        operation, linked = "link_check", True
    else:
        await session.delete(link)
        operation, linked = "unlink_check", False
    card.version += 1
    check.version += 1
    await record_card_event(session, card, operation, actor, before, new_correlation_id())
    await bump_workspace(session)
    return linked


async def set_card_parent(
    session: AsyncSession,
    card_id: int,
    parent_id: int | None,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> Card:
    """Attach a Card to a parent (or make it root-level) with full hierarchy repair.

    A Goal placed under a Goal becomes a Subgoal: the tree has no Goal below a Goal, and
    the placement was asked for. That and `delete_one_card` are the two places a kind
    changes on its own, and both write an `edit_kind` event saying so.
    """
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    becomes_subgoal = card.kind == CardKind.GOAL.value and parent_id is not None
    await validate_parent(
        session, CardKind.SUBGOAL if becomes_subgoal else card.kind, parent_id
    )
    if becomes_subgoal and await holds_subgoals(session, card.id):
        raise DomainError("A Goal with Subgoals under it cannot become a Subgoal")
    if card.parent_id == parent_id:
        return card

    previous_parent_id = card.parent_id
    before = card_snapshot(card)
    card.parent_id = parent_id
    if becomes_subgoal:
        card.kind = CardKind.SUBGOAL.value
    card.version += 1
    await record_card_event(
        session,
        card,
        "edit_kind" if becomes_subgoal else "set_parent",
        actor,
        before,
        new_correlation_id(),
    )
    await propagate_ancestors(session, previous_parent_id)
    await propagate_ancestors(session, parent_id)
    await bump_workspace(session)
    return card


async def validate_parent(
    session: AsyncSession,
    kind: CardKind | str,
    parent_id: int | None,
) -> Card | None:
    kind = CardKind(kind)
    if parent_id is None:
        if kind is CardKind.SUBGOAL:
            raise DomainError("A Subgoal may only be placed under a Goal")
        return None
    if kind is CardKind.GOAL:
        raise DomainError("A Goal is created root-level")
    parent = await session.get(Card, parent_id)
    if parent is None or parent.archived_at is not None:
        raise DomainError("Parent does not exist or is archived")
    if parent.kind == CardKind.ACTION.value:
        raise DomainError("An Action cannot have children")
    if kind is CardKind.SUBGOAL and parent.kind != CardKind.GOAL.value:
        raise DomainError("A Subgoal may only be placed under a Goal")
    return parent


async def holds_subgoals(session: AsyncSession, card_id: int) -> bool:
    return any(
        child.kind == CardKind.SUBGOAL.value for child in await card_children(session, card_id)
    )


def validate_action_fields(
    kind: CardKind | str,
    effort_points: float | None,
    repeatable: bool,
    categories: set[str] | None = None,
    energy_types: set[str] | None = None,
    *,
    blocked: bool = False,
) -> None:
    kind = CardKind(kind)
    if kind is CardKind.ACTION:
        if effort_points not in EFFORT_POINTS:
            raise DomainError(
                "An Action needs effort points: "
                + ", ".join(effort_label(rung) for rung in sorted(EFFORT_POINTS))
            )
        return
    if effort_points is not None or repeatable or categories or energy_types or blocked:
        raise DomainError("Goal and Subgoal cards cannot have Action-only fields")


def validate_blocked_fields(blocked: bool, description: str | None) -> None:
    if blocked and not (description or "").strip():
        raise DomainError("A blocked Card needs a blocked description")


async def record_card_event(
    session: AsyncSession,
    card: Card,
    operation: str,
    actor: ActorType,
    before: dict[str, Any] | None,
    correlation_id: str,
) -> None:
    workspace = await require_workspace(session)
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


async def move_card(
    session: AsyncSession,
    card_id: int,
    stage: CardStage,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> OperationResult:
    """Put one Action on a live stage, or bring a finished one back."""
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    if card.kind != CardKind.ACTION.value:
        raise DomainError("Only an Action has a stage; a parent shows what its Actions are in")
    if card.is_closed_repeat():
        # Reopening it would run two instances of one series at once.
        raise DomainError("A closed repeating Action cannot be reopened")
    if stage in TERMINAL_STAGES:
        # finish_action owns completion timestamps, Sprint results, Checks and repeat
        # successors.  Moving here would set the stage and skip all of that accounting.
        raise DomainError("An Action reaches Done through finish_action")
    previous = CardStage(card.effective_stage)
    reopening = previous in TERMINAL_STAGES
    before = card_snapshot(card)
    result = OperationResult(card_ids=[card.id])
    if card.blocked:
        result.warnings.append(f"Blocked: {card.blocked_description}")
    if reopening:
        card.completed_at = None
        card.archived_at = None
        await reopen_checks(session, card.id)
    card.manual_stage = stage.value
    card.effective_stage = stage.value
    card.version += 1
    await record_card_event(session, card, "move", actor, before, new_correlation_id())
    await sync_commitment_for_stage(session, card, previous)
    result.ancestor_ids = await propagate_ancestors(session, card.parent_id)
    await bump_workspace(session)
    return result


async def _copy_repeat_successor(session: AsyncSession, card: Card, live_stage: CardStage) -> Card:
    series_id = card.repeat_series_id or card.id
    card.repeat_series_id = series_id
    successor = Card(
        parent_id=card.parent_id,
        kind=card.kind,
        title=card.title,
        note=card.note,
        manual_stage=live_stage.value,
        effective_stage=live_stage.value,
        priority=card.priority,
        hard_time=card.hard_time,
        blocked=card.blocked,
        blocked_description=card.blocked_description,
        effort_points=card.effort_points,
        repeatable=True,
        repeat_series_id=series_id,
        source_instance_id=card.id,
    )
    session.add(successor)
    await session.flush()
    for link in await session.scalars(select(CardValue).where(CardValue.card_id == card.id)):
        session.add(CardValue(card_id=successor.id, value_id=link.value_id))
    for link in await session.scalars(select(CardTag).where(CardTag.card_id == card.id)):
        session.add(CardTag(card_id=successor.id, tag_id=link.tag_id))
    for link in await session.scalars(select(CardCategory).where(CardCategory.card_id == card.id)):
        session.add(CardCategory(card_id=successor.id, category=link.category))
    for link in await session.scalars(
        select(CardEnergyType).where(CardEnergyType.card_id == card.id)
    ):
        session.add(CardEnergyType(card_id=successor.id, energy_type=link.energy_type))
    await clone_checks_for_successor(session, card.id, successor.id)
    await sync_commitment_for_stage(session, successor)
    return successor


async def finish_action(
    session: AsyncSession,
    card_id: int,
    *,
    actor: ActorType = ActorType.USER_UI,
    check_outcomes: dict[int, Any] | None = None,
) -> OperationResult:
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    if card.kind != CardKind.ACTION.value:
        raise DomainError("Only Actions are finished directly")
    if CardStage(card.effective_stage) in TERMINAL_STAGES:
        raise DomainError("Action is already terminal")
    # Asked before anything is written, so a refusal leaves the Action where it was.
    resolutions = await require_check_answers(session, card.id, check_outcomes)
    previous_live_stage = CardStage(card.effective_stage)
    before = card_snapshot(card)
    card.manual_stage = CardStage.DONE.value
    card.effective_stage = CardStage.DONE.value
    card.completed_at = utcnow()
    card.version += 1
    correlation_id = new_correlation_id()
    await record_card_event(
        session, card, CardStage.DONE.value, actor, before, correlation_id
    )
    await record_sprint_result(session, card.id)
    result = OperationResult(card_ids=[card.id])
    if card.blocked:
        result.warnings.append(f"Blocked: {card.blocked_description}")
    await settle_checks(session, card.id, resolutions, actor=actor)
    if card.repeatable:
        successor = await _copy_repeat_successor(session, card, previous_live_stage)
        result.successor_ids.append(successor.id)
    result.ancestor_ids = await propagate_ancestors(session, card.parent_id)
    await bump_workspace(session)
    return result


async def archive_subtree(session: AsyncSession, card_id: int, archive: bool = True) -> list[int]:
    """Archive or restore a branch by its Actions; a Goal and a Subgoal follow from theirs."""
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    if archive and CardStage(card.effective_stage) not in TERMINAL_STAGES:
        raise DomainError("Only a Card that is Done may be archived")
    stamp = utcnow() if archive else None
    correlation_id = new_correlation_id()
    actions = (
        [card] if card.kind == CardKind.ACTION.value else await branch_actions(session, card.id)
    )
    for action in actions:
        before = card_snapshot(action)
        action.archived_at = stamp
        action.version += 1
        await record_card_event(
            session,
            action,
            "archive" if archive else "restore",
            ActorType.USER_UI,
            before,
            correlation_id,
        )
    changed = await settle_archive(session, actions)
    await bump_workspace(session)
    return changed


async def _purge_cards(session: AsyncSession, ids: list[int]) -> None:
    # Every link, commitment and event is deleted by name rather than left to the FK
    # cascade, which is a connection pragma and not guaranteed here.
    await delete_checks_of_cards(session, ids)
    await delete_commitments_of_cards(session, ids)
    for model in (CardValue, CardTag, CardCategory, CardEnergyType, CardEvent):
        await session.execute(delete(model).where(model.card_id.in_(ids)))
    await session.execute(delete(Card).where(Card.id.in_(ids)))


async def delete_subtree(session: AsyncSession, card_id: int) -> int:
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    parent_id = card.parent_id
    ids: list[int] = []

    async def collect(node: Card) -> None:
        ids.append(node.id)
        for child in await session.scalars(select(Card).where(Card.parent_id == node.id)):
            await collect(child)

    await collect(card)
    await _purge_cards(session, ids)
    await propagate_ancestors(session, parent_id)
    await bump_workspace(session)
    return len(ids)


async def delete_one_card(session: AsyncSession, card_id: int) -> int:
    """Delete one Card and leave what was under it standing where the tree allows.

    A Subgoal cannot stand without a Goal over it, so one that loses its Goal becomes a
    Goal itself. That and `set_card_parent` are the two places a kind changes on its own,
    and it is recorded like any other edit rather than happening silently.
    """
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    parent_id = card.parent_id
    correlation_id = new_correlation_id()
    for child in await session.scalars(select(Card).where(Card.parent_id == card.id)):
        before = card_snapshot(child)
        child.parent_id = None
        promoted = child.kind == CardKind.SUBGOAL.value
        if promoted:
            child.kind = CardKind.GOAL.value
        child.version += 1
        await record_card_event(
            session,
            child,
            "edit_kind" if promoted else "set_parent",
            ActorType.USER_UI,
            before,
            correlation_id,
        )
    await _purge_cards(session, [card_id])
    await propagate_ancestors(session, parent_id)
    await bump_workspace(session)
    return 1


async def archive_settled_cards(session: AsyncSession, cutoff: datetime) -> list[int]:
    """Archive every Action that closed on or before the cutoff; the parents follow."""
    stamp = utcnow()
    actions = list(
        await session.scalars(
            select(Card).where(
                Card.archived_at.is_(None),
                Card.kind == CardKind.ACTION.value,
                Card.effective_stage.in_([stage.value for stage in TERMINAL_STAGES]),
                Card.completed_at.is_not(None),
                Card.completed_at <= cutoff,
            )
        )
    )
    for action in actions:
        action.archived_at = stamp
        action.version += 1
    return await settle_archive(session, actions)
