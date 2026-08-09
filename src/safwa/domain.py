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
    Category,
    EnergyType,
    Priority,
    WorkspaceMode,
)
from .models import (
    Card,
    CardCategory,
    CardEnergyType,
    CardEvent,
    CardTag,
    CardValue,
    FeedbackQueue,
    ReminderState,
    SavedRequest,
    Sprint,
    SprintCommitment,
    Tag,
    UserProfile,
    Value,
    Workspace,
    new_correlation_id,
)
from .saved_requests import RequestQueryError, normalize_request_sql


class DomainError(ValueError):
    pass


class StaleStateError(DomainError):
    pass


@dataclass
class OperationResult:
    card_ids: list[int] = field(default_factory=list)
    ancestor_ids: list[int] = field(default_factory=list)
    successor_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def utcnow() -> datetime:
    return datetime.now(UTC)


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
    await session.flush()
    return workspace


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
        effort_points = None
        repeatable = False
        category_values.clear()
        energy_values.clear()
    validate_action_fields(
        card_kind,
        effort_points,
        repeatable,
        category_values,
        energy_values,
    )
    validate_blocked_fields(blocked, clean_description)
    await validate_parent(session, card_kind, parent_id)

    for value_id in value_ids or set():
        value = await session.get(Value, value_id)
        if value is None or value.archived_at is not None:
            raise DomainError(f"Value #{value_id} does not exist or is archived")
    for tag_id in tag_ids or set():
        tag = await session.get(Tag, tag_id)
        if tag is None or tag.archived_at is not None:
            raise DomainError(f"Tag #{tag_id} does not exist or is archived")

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
    for value_id in sorted(value_ids or set()):
        session.add(CardValue(card_id=card.id, value_id=value_id))
    for tag_id in sorted(tag_ids or set()):
        session.add(CardTag(card_id=card.id, tag_id=tag_id))
    await _record_event(session, card, "create", actor, None, new_correlation_id())
    if card_kind is CardKind.ACTION:
        await _sync_commitment_for_stage(session, card)
    await propagate_ancestors(session, parent_id)
    await _bump_workspace(session)
    return card


async def create_tag(session: AsyncSession, name: str, description: str | None = None) -> Tag:
    normalized = name.strip()
    if not normalized:
        raise DomainError("Tag name cannot be empty")
    existing = await session.scalar(select(Tag).where(Tag.name.collate("NOCASE") == normalized))
    if existing is not None:
        if existing.archived_at is None:
            raise DomainError("A Tag with this name already exists")
        existing.archived_at = None
        if description is not None:
            existing.description = description.strip()
        existing.version += 1
        await _bump_workspace(session)
        return existing
    tag = Tag(name=normalized, description=(description or "").strip())
    session.add(tag)
    await _bump_workspace(session)
    return tag


async def update_tag_fields(
    session: AsyncSession,
    tag_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Tag:
    tag = await session.get(Tag, tag_id)
    if tag is None or tag.archived_at is not None:
        raise DomainError("Tag does not exist or is archived")
    if name is not None:
        normalized = name.strip()
        if not normalized:
            raise DomainError("Tag name cannot be empty")
        duplicate = await session.scalar(
            select(Tag).where(
                Tag.name.collate("NOCASE") == normalized,
                Tag.id != tag.id,
            )
        )
        if duplicate is not None:
            raise DomainError("A Tag with this name already exists")
        tag.name = normalized
    if description is not None:
        tag.description = description.strip()
    tag.version += 1
    await _bump_workspace(session)
    return tag


async def archive_tag(session: AsyncSession, tag_id: int) -> tuple[Tag, int]:
    """Archive a Tag and remove every direct Card link in the same transaction."""
    tag = await session.get(Tag, tag_id)
    if tag is None or tag.archived_at is not None:
        raise DomainError("Tag does not exist or is archived")
    linked_count = int(
        await session.scalar(
            select(func.count()).select_from(CardTag).where(CardTag.tag_id == tag.id)
        )
        or 0
    )
    await session.execute(delete(CardTag).where(CardTag.tag_id == tag.id))
    tag.archived_at = utcnow()
    tag.version += 1
    await _bump_workspace(session)
    return tag, linked_count


async def create_saved_request(
    session: AsyncSession,
    name: str,
    query_sql: str,
    description: str | None = None,
) -> SavedRequest:
    normalized_name = name.strip()
    if not normalized_name:
        raise DomainError("Request name cannot be empty")
    try:
        normalized_query = normalize_request_sql(query_sql)
    except RequestQueryError as error:
        raise DomainError(str(error)) from error
    existing = await session.scalar(
        select(SavedRequest).where(SavedRequest.name.collate("NOCASE") == normalized_name)
    )
    if existing is not None:
        if existing.archived_at is None:
            raise DomainError("A Request with this name already exists")
        existing.archived_at = None
        existing.query_sql = normalized_query
        if description is not None:
            existing.description = description.strip()
        existing.version += 1
        await _bump_workspace(session)
        return existing
    request = SavedRequest(
        name=normalized_name,
        description=(description or "").strip(),
        query_sql=normalized_query,
    )
    session.add(request)
    await session.flush()
    await _bump_workspace(session)
    return request


async def update_saved_request(
    session: AsyncSession,
    request_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    query_sql: str | None = None,
) -> SavedRequest:
    request = await session.get(SavedRequest, request_id)
    if request is None or request.archived_at is not None:
        raise DomainError("Request does not exist or is archived")
    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            raise DomainError("Request name cannot be empty")
        duplicate = await session.scalar(
            select(SavedRequest).where(
                SavedRequest.name.collate("NOCASE") == normalized_name,
                SavedRequest.id != request.id,
            )
        )
        if duplicate is not None:
            raise DomainError("A Request with this name already exists")
        request.name = normalized_name
    if description is not None:
        request.description = description.strip()
    if query_sql is not None:
        try:
            request.query_sql = normalize_request_sql(query_sql)
        except RequestQueryError as error:
            raise DomainError(str(error)) from error
    request.version += 1
    await _bump_workspace(session)
    return request


async def archive_saved_request(session: AsyncSession, request_id: int) -> SavedRequest:
    request = await session.get(SavedRequest, request_id)
    if request is None or request.archived_at is not None:
        raise DomainError("Request does not exist or is archived")
    request.archived_at = utcnow()
    request.version += 1
    await _bump_workspace(session)
    return request


async def create_value(
    session: AsyncSession,
    name: str,
    description: str | None = None,
    *,
    active: bool | None = None,
) -> Value:
    normalized = name.strip()
    if not normalized:
        raise DomainError("Value name cannot be empty")
    existing = await session.scalar(select(Value).where(Value.name.collate("NOCASE") == normalized))
    if existing is not None:
        if existing.archived_at is None:
            raise DomainError("A Value with this name already exists")
        existing.archived_at = None
        if description is not None:
            existing.description = description.strip()
        if active is not None:
            existing.active = active
        existing.version += 1
        await _bump_workspace(session)
        return existing
    value = Value(
        name=normalized,
        description=(description or "").strip(),
        active=bool(active),
    )
    session.add(value)
    await _bump_workspace(session)
    return value


async def update_value_fields(
    session: AsyncSession,
    value_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    active: bool | None = None,
) -> Value:
    value = await session.get(Value, value_id)
    if value is None or value.archived_at is not None:
        raise DomainError("Value does not exist or is archived")
    if name is not None:
        normalized = name.strip()
        if not normalized:
            raise DomainError("Value name cannot be empty")
        duplicate = await session.scalar(
            select(Value).where(
                Value.name.collate("NOCASE") == normalized,
                Value.id != value.id,
            )
        )
        if duplicate is not None:
            raise DomainError("A Value with this name already exists")
        value.name = normalized
    if description is not None:
        value.description = description.strip()
    if active is not None:
        value.active = active
    value.version += 1
    await _bump_workspace(session)
    return value


async def archive_value(session: AsyncSession, value_id: int) -> tuple[Value, int]:
    """Archive a Value and remove every direct Card link in the same transaction."""
    value = await session.get(Value, value_id)
    if value is None or value.archived_at is not None:
        raise DomainError("Value does not exist or is archived")
    linked_count = int(
        await session.scalar(
            select(func.count()).select_from(CardValue).where(CardValue.value_id == value.id)
        )
        or 0
    )
    await session.execute(delete(CardValue).where(CardValue.value_id == value.id))
    value.active = False
    value.archived_at = utcnow()
    value.version += 1
    await _bump_workspace(session)
    return value, linked_count


async def set_value_focus(
    session: AsyncSession, value_id: int, active: bool | None = None
) -> Value:
    value = await session.get(Value, value_id)
    if value is None or value.archived_at is not None:
        raise DomainError("Value does not exist or is archived")
    value.active = (not value.active) if active is None else active
    value.version += 1
    await _bump_workspace(session)
    return value


async def update_profile(session: AsyncSession, **fields: Any) -> UserProfile:
    allowed = {
        "about_me",
        "advisor_instructions",
        "wake_time",
        "bed_time",
        "quiet_start",
        "quiet_end",
        "morning_checkin",
        "evening_checkin",
        "capacity_effort_points",
        "proactive_limit",
        "reminder_cooldown_minutes",
        "reminders_enabled",
        "weekend_enabled",
    }
    unknown = set(fields).difference(allowed)
    if unknown:
        raise DomainError("Unsupported profile field: " + ", ".join(sorted(unknown)))
    profile = await session.get(UserProfile, 1)
    if profile is None:
        raise DomainError("User profile is not initialized")
    for field_name, value in fields.items():
        setattr(profile, field_name, value)
    await _bump_workspace(session)
    return profile


async def snooze_reminders(session: AsyncSession, until: datetime) -> ReminderState:
    state = await session.get(ReminderState, "global")
    if state is None:
        state = ReminderState(kind="global")
        session.add(state)
    state.snoozed_until = until
    await _bump_workspace(session)
    return state


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
    validate_blocked_fields(card.blocked, card.blocked_description)
    card.version += 1
    await _record_event(
        session, card, f"edit_{field}", ActorType.USER_UI, before, new_correlation_id()
    )
    await _bump_workspace(session)
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
    before = card_snapshot(card)
    for name, value in fields.items():
        if name in {"title", "note"}:
            value = str(value).strip()
        if name == "title" and not value:
            raise DomainError("Card title cannot be empty")
        setattr(card, name, value)
    validate_action_fields(card.kind, card.effort_points, card.repeatable)
    if not card.blocked:
        card.blocked_description = ""
    validate_blocked_fields(card.blocked, card.blocked_description)
    card.version += 1
    await _record_event(session, card, "update", actor, before, new_correlation_id())
    await _bump_workspace(session)
    return card


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
    await validate_parent(session, card.kind, parent_id, card_id=card.id)
    if card.parent_id == parent_id:
        return card

    previous_parent_id = card.parent_id
    before = card_snapshot(card)
    card.parent_id = parent_id
    card.version += 1
    await _record_event(session, card, "set_parent", actor, before, new_correlation_id())
    await propagate_ancestors(session, previous_parent_id)
    await propagate_ancestors(session, parent_id)
    await _bump_workspace(session)
    return card


async def toggle_card_value(
    session: AsyncSession, card_id: int, value_id: int, *, actor: ActorType = ActorType.USER_UI
) -> bool:
    """Toggle a direct Value link and return whether it is now linked."""
    card = await session.get(Card, card_id)
    value = await session.get(Value, value_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    if value is None or value.archived_at is not None:
        raise DomainError("Value does not exist or is archived")
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
    await _record_event(session, card, operation, actor, before, new_correlation_id())
    await _bump_workspace(session)
    return linked


async def toggle_card_tag(
    session: AsyncSession, card_id: int, tag_id: int, *, actor: ActorType = ActorType.USER_UI
) -> bool:
    """Toggle a direct Tag link and return whether it is now linked."""
    card = await session.get(Card, card_id)
    tag = await session.get(Tag, tag_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    if tag is None or tag.archived_at is not None:
        raise DomainError("Tag does not exist or is archived")
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
    await _record_event(session, card, operation, actor, before, new_correlation_id())
    await _bump_workspace(session)
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
    await _record_event(session, card, operation, actor, before, new_correlation_id())
    await _bump_workspace(session)
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
    await _record_event(session, card, operation, actor, before, new_correlation_id())
    await _bump_workspace(session)
    return linked


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
    parent_id: int | None,
    *,
    card_id: int | None = None,
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


def validate_blocked_fields(blocked: bool, description: str | None) -> None:
    if blocked and not (description or "").strip():
        raise DomainError("A blocked Card needs a blocked description")


async def _children(session: AsyncSession, card_id: int) -> list[Card]:
    return list(
        await session.scalars(
            select(Card).where(Card.parent_id == card_id, Card.archived_at.is_(None))
        )
    )


async def effective_value_ids(session: AsyncSession, card_id: int) -> set[int]:
    """Direct Values plus descendant Values, without duplicating stored links."""
    pending = [card_id]
    card_ids: list[int] = []
    while pending:
        current = pending.pop()
        card_ids.append(current)
        pending.extend(child.id for child in await _children(session, current))
    return set(
        await session.scalars(select(CardValue.value_id).where(CardValue.card_id.in_(card_ids)))
    )


async def card_progress(session: AsyncSession, card_id: int) -> dict[str, int]:
    """Return recursive Action effort and direct-child completion for a Goal or Idea."""
    cards = list(await session.scalars(select(Card).where(Card.archived_at.is_(None))))
    children_by_parent: dict[int, list[Card]] = {}
    for card in cards:
        if card.parent_id is not None:
            children_by_parent.setdefault(card.parent_id, []).append(card)
    direct_children = children_by_parent.get(card_id, [])
    descendants: list[Card] = []
    pending = list(direct_children)
    while pending:
        descendant = pending.pop()
        descendants.append(descendant)
        pending.extend(children_by_parent.get(descendant.id, []))
    actions = [card for card in descendants if card.kind == CardKind.ACTION.value]
    return {
        "completed_effort": sum(
            card.effort_points or 0
            for card in actions
            if card.effective_stage == CardStage.DONE.value
        ),
        "total_effort": sum(card.effort_points or 0 for card in actions),
        "completed_children": sum(
            child.effective_stage == CardStage.DONE.value for child in direct_children
        ),
        "total_children": len(direct_children),
    }


def aggregate_child_stages(children: list[Card]) -> CardStage:
    stages = [CardStage(child.effective_stage) for child in children]
    live = [stage for stage in stages if stage not in TERMINAL_STAGES]
    if live:
        return max(live, key=lambda stage: LIVE_STAGE_PRECEDENCE[stage])
    if stages and all(stage is CardStage.CANCELLED for stage in stages):
        return CardStage.CANCELLED
    return CardStage.DONE


async def propagate_ancestors(session: AsyncSession, start_parent_id: int | None) -> list[int]:
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
    card_id: int,
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
    correlation_id = new_correlation_id()
    result = OperationResult(card_ids=[card.id])
    if card.blocked:
        result.warnings.append(f"Blocked: {card.blocked_description}")

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
    await _sync_commitment_for_stage(session, successor)
    return successor


async def finish_action(
    session: AsyncSession,
    card_id: int,
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
    correlation_id = new_correlation_id()
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
    if card.blocked:
        result.warnings.append(f"Blocked: {card.blocked_description}")
    if terminal_stage is CardStage.DONE:
        session.add(FeedbackQueue(card_id=card.id))
    if card.repeatable:
        successor = await _copy_repeat_successor(session, card, previous_live_stage)
        result.successor_ids.append(successor.id)
    result.ancestor_ids = await propagate_ancestors(session, card.parent_id)
    await _bump_workspace(session)
    return result


async def set_feedback(session: AsyncSession, queue_id: int, liked: bool) -> Card:
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


async def sprint_metrics(session: AsyncSession, sprint_id: int) -> dict[str, int]:
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


async def archive_subtree(session: AsyncSession, card_id: int, archive: bool = True) -> list[int]:
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    changed: list[str] = []
    stamp = utcnow() if archive else None
    correlation_id = new_correlation_id()

    async def visit(node: Card) -> None:
        before = card_snapshot(node)
        node.archived_at = stamp
        node.version += 1
        await _record_event(
            session,
            node,
            "archive" if archive else "restore",
            ActorType.USER_UI,
            before,
            correlation_id,
        )
        changed.append(node.id)
        for child in await session.scalars(select(Card).where(Card.parent_id == node.id)):
            await visit(child)

    await visit(card)
    await propagate_ancestors(session, card.parent_id)
    await _bump_workspace(session)
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
    await session.execute(delete(Card).where(Card.id.in_(ids)))
    await propagate_ancestors(session, parent_id)
    await _bump_workspace(session)
    return len(ids)
