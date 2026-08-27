"""Writing a Card: what one is, where it may sit, and the fields its kind may carry.

A Sprint commitment follows an Action's stage, and every writer of that stage is in this
file: each one calls Planning's door afterwards, and no Card row here ever touches a
commitment itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...enums import (
    ActorType,
    CardKind,
    Category,
    EnergyType,
    Priority,
)
from ...foundation.clock import utcnow
from ...foundation.errors import DomainError
from ...foundation.workspace import bump_workspace, require_workspace
from ...models import CardTag, CardValue, Check
from ..checks.api import (
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
from ..values.api import Value, attach_values, unlinkable_value_id
from .model import (
    LIVE_STAGE_PRECEDENCE,
    TERMINAL_STAGES,
    Card,
    CardCategory,
    CardCheck,
    CardEnergyType,
    CardEvent,
    CardStage,
    is_closed_repeat,
    new_correlation_id,
)

# The allowed Action effort scale.  Mirrored by the Literal in ai/contracts.py.
EFFORT_POINTS = {1, 2, 3, 5, 8, 13}
# The fields an Action alone carries. On a Goal and an Idea two of them are derived, so
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
    effort_points: int | None = None,
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
            raise DomainError("Goal and Idea cards cannot have Action-only fields")
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
    """Attach a Card to a parent (or make it root-level) with full hierarchy repair."""
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    await validate_parent(session, card.kind, parent_id)
    if card.parent_id == parent_id:
        return card

    previous_parent_id = card.parent_id
    before = card_snapshot(card)
    card.parent_id = parent_id
    card.version += 1
    await record_card_event(session, card, "set_parent", actor, before, new_correlation_id())
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
        return None
    if kind is CardKind.GOAL:
        raise DomainError("A Goal must be root-level")
    parent = await session.get(Card, parent_id)
    if parent is None or parent.archived_at is not None:
        raise DomainError("Parent does not exist or is archived")
    if parent.kind == CardKind.ACTION.value:
        raise DomainError("An Action cannot have children")
    if kind is CardKind.IDEA and parent.kind != CardKind.GOAL.value:
        raise DomainError("An Idea may only be placed under a Goal")
    return parent


def validate_action_fields(
    kind: CardKind | str,
    effort_points: int | None,
    repeatable: bool,
    categories: set[str] | None = None,
    energy_types: set[str] | None = None,
    *,
    blocked: bool = False,
) -> None:
    kind = CardKind(kind)
    if kind is CardKind.ACTION:
        if effort_points not in EFFORT_POINTS:
            raise DomainError("An Action needs effort points: 1, 2, 3, 5, 8, or 13")
        return
    if effort_points is not None or repeatable or categories or energy_types or blocked:
        raise DomainError("Goal and Idea cards cannot have Action-only fields")


def validate_blocked_fields(blocked: bool, description: str | None) -> None:
    if blocked and not (description or "").strip():
        raise DomainError("A blocked Card needs a blocked description")


def aggregate_child_stages(children: list[Card]) -> CardStage:
    stages = [CardStage(child.effective_stage) for child in children]
    live = [stage for stage in stages if stage not in TERMINAL_STAGES]
    if live:
        return max(live, key=lambda stage: LIVE_STAGE_PRECEDENCE[stage])
    if all(stage is CardStage.CANCELLED for stage in stages):
        return CardStage.CANCELLED
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
    """What a Goal or an Idea shows: the stage, the block, the effort and the archive.

    Every child already carries its own derived values, so a parent adds up the row below
    it and the recursion reaches the Actions on its own. Reading the branch's Actions
    directly would skip an Idea with nothing in it, and a Goal would call itself Done over
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


async def card_children(session: AsyncSession, card_id: int) -> list[Card]:
    """Every Card under this one. An archived child is shown marked, not left out."""
    return list(await session.scalars(select(Card).where(Card.parent_id == card_id)))


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
    if is_closed_repeat(card):
        # Reopening it would run two instances of one series at once.
        raise DomainError("A closed repeating Action cannot be reopened")
    if stage in TERMINAL_STAGES:
        # finish_action owns completion timestamps, Sprint results, Checks and repeat
        # successors.  Moving here would set the stage and skip all of that accounting.
        raise DomainError("An Action reaches Done or Cancelled through finish_action")
    previous = CardStage(card.effective_stage)
    reopening = previous in TERMINAL_STAGES
    before = card_snapshot(card)
    result = OperationResult(card_ids=[card.id])
    if card.blocked:
        result.warnings.append(f"Blocked: {card.blocked_description}")
    if reopening:
        card.completed_at = None
        card.cancelled_at = None
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
    terminal_stage: CardStage,
    *,
    actor: ActorType = ActorType.USER_UI,
    check_outcomes: dict[int, Any] | None = None,
) -> OperationResult:
    if terminal_stage not in TERMINAL_STAGES:
        raise DomainError("Finish stage must be Done or Cancelled")
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    if card.kind != CardKind.ACTION.value:
        raise DomainError("Only Actions are finished directly")
    if CardStage(card.effective_stage) in TERMINAL_STAGES:
        raise DomainError("Action is already terminal")
    # Done is gated on the Check series this Card has no answer for; Cancelling is not,
    # because abandoning a Card with unanswered Checks is legitimate.  Asked before
    # anything is written, so a refusal leaves the Action where it was.
    resolutions = await require_check_answers(
        session, card.id, check_outcomes, gated=terminal_stage is CardStage.DONE
    )
    previous_live_stage = CardStage(card.effective_stage)
    before = card_snapshot(card)
    now = utcnow()
    card.manual_stage = terminal_stage.value
    card.effective_stage = terminal_stage.value
    card.completed_at = now if terminal_stage is CardStage.DONE else None
    card.cancelled_at = now if terminal_stage is CardStage.CANCELLED else None
    card.version += 1
    correlation_id = new_correlation_id()
    await record_card_event(session, card, terminal_stage.value, actor, before, correlation_id)
    await record_sprint_result(session, card.id, terminal_stage)
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


async def card_progress(session: AsyncSession, card_id: int) -> dict[str, int]:
    """Completed Action effort and direct-child completion for a Goal or an Idea.

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


async def archive_subtree(session: AsyncSession, card_id: int, archive: bool = True) -> list[int]:
    """Archive or restore a branch by its Actions; a Goal and an Idea follow from theirs."""
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    if archive and CardStage(card.effective_stage) not in TERMINAL_STAGES:
        raise DomainError("Only a Card that is Done or Cancelled may be archived")
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
    await delete_checks_of_cards(session, ids)
    # Every link, commitment and event is deleted by name rather than left to the FK
    # cascade, which is a connection pragma and not guaranteed here.
    await delete_commitments_of_cards(session, ids)
    for model in (CardValue, CardTag, CardCategory, CardEnergyType, CardEvent):
        await session.execute(delete(model).where(model.card_id.in_(ids)))
    await session.execute(delete(Card).where(Card.id.in_(ids)))
    await propagate_ancestors(session, parent_id)
    await bump_workspace(session)
    return len(ids)


async def archive_settled_cards(session: AsyncSession, cutoff: datetime) -> list[int]:
    """Archive every Action that closed on or before the cutoff; the parents follow."""
    stamp = utcnow()
    actions = list(
        await session.scalars(
            select(Card).where(
                Card.archived_at.is_(None),
                Card.kind == CardKind.ACTION.value,
                Card.effective_stage.in_([stage.value for stage in TERMINAL_STAGES]),
                func.coalesce(Card.completed_at, Card.cancelled_at).is_not(None),
                func.coalesce(Card.completed_at, Card.cancelled_at) <= cutoff,
            )
        )
    )
    for action in actions:
        action.archived_at = stamp
        action.version += 1
    return await settle_archive(session, actions)
