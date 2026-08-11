from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .constants import EFFORT_POINTS, SPRINT_LENGTH_DAYS
from .enums import (
    LIVE_STAGE_PRECEDENCE,
    TERMINAL_STAGES,
    ActorType,
    CardKind,
    CardStage,
    Category,
    CheckOutcome,
    EnergyType,
    Priority,
    WorkspaceMode,
)
from .models import (
    Card,
    CardCategory,
    CardCheck,
    CardEnergyType,
    CardEvent,
    CardTag,
    CardValue,
    Check,
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


def listed(value: Any) -> list[Any]:
    """Accept either one reference or a list of them from a proposal payload."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@dataclass(frozen=True)
class ReferenceSpec:
    """One Card relationship: where it lives in a payload and how it is written.

    ``singular_key`` doubles as the link table's own column name, so the same spec
    addresses the payload, the lookup and the junction row.
    """

    singular_key: str
    plural_key: str
    query_key: str
    model: type[Tag] | type[Value] | type[Check]
    label: str
    link_model: type[CardTag] | type[CardValue] | type[CardCheck]
    toggle: Callable[..., Awaitable[bool]]
    # A Check is named by `title`, so the column a query_key resolves against varies.
    name_attr: str = "name"

    def mentioned_in(self, values: dict[str, Any]) -> bool:
        return bool({self.singular_key, self.plural_key, self.query_key} & values.keys())

    def link_key(self, card_id: int, entity_id: int) -> dict[str, int]:
        return {"card_id": card_id, self.singular_key: entity_id}

    @property
    def link_column(self) -> Any:
        return self.link_model.__table__.c[self.singular_key]

    @property
    def name_column(self) -> Any:
        return getattr(self.model, self.name_attr)


@dataclass(frozen=True)
class ResolvedReferences:
    """What a payload's Tag/Value references point at, and what could not be resolved."""

    ids: set[int]
    unknown_ids: tuple[int, ...] = ()
    missing: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    blank: bool = False

    @property
    def unresolved(self) -> tuple[str, ...]:
        return (*self.missing, *self.ambiguous)


async def resolve_references(
    session: AsyncSession, spec: ReferenceSpec, values: dict[str, Any]
) -> ResolvedReferences:
    """Resolve one relationship's IDs and exact names against committed data.

    Preparation, approval and the review screen all need the same answer; they differ
    only in how they report what did not resolve.
    """
    ids: set[int] = set()
    unknown_ids: list[int] = []
    for raw_id in [*listed(values.get(spec.singular_key)), *listed(values.get(spec.plural_key))]:
        entity_id = int(raw_id)
        entity = await session.get(spec.model, entity_id)
        if entity is None or entity.archived_at is not None:
            unknown_ids.append(entity_id)
        else:
            ids.add(entity_id)

    missing: list[str] = []
    ambiguous: list[str] = []
    blank = False
    for raw_name in listed(values.get(spec.query_key)):
        name = str(raw_name).strip()
        if not name:
            blank = True
            continue
        matches = list(
            await session.scalars(
                select(spec.model).where(
                    spec.name_column.collate("NOCASE") == name,
                    spec.model.archived_at.is_(None),
                )
            )
        )
        if len(matches) == 1:
            ids.add(matches[0].id)
        elif matches:
            ambiguous.append(name)
        else:
            missing.append(name)
    return ResolvedReferences(ids, tuple(unknown_ids), tuple(missing), tuple(ambiguous), blank)


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
    for check_id in check_ids or set():
        check = await session.get(Check, check_id)
        if check is None or check.archived_at is not None:
            raise DomainError(f"Check #{check_id} does not exist or is archived")

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
    for check_id in sorted(check_ids or set()):
        session.add(CardCheck(card_id=card.id, check_id=check_id))
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
    """Set or flip Value focus through the single Value write path."""
    value = await session.get(Value, value_id)
    if value is None or value.archived_at is not None:
        raise DomainError("Value does not exist or is archived")
    return await update_value_fields(
        session, value_id, active=(not value.active) if active is None else active
    )


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
    if not card.blocked:
        card.blocked_description = ""
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
        if name == "priority":
            value = Priority(value).value
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


async def card_checks(session: AsyncSession, card_id: int) -> list[Check]:
    return list(
        await session.scalars(
            select(Check)
            .join(CardCheck, CardCheck.check_id == Check.id)
            .where(CardCheck.card_id == card_id, Check.archived_at.is_(None))
            .order_by(Check.id)
        )
    )


async def pending_checks(session: AsyncSession, card_id: int) -> list[Check]:
    """Pending is derived, never stored: a Check that has no outcome yet."""
    return list(
        await session.scalars(
            select(Check)
            .join(CardCheck, CardCheck.check_id == Check.id)
            .where(
                CardCheck.card_id == card_id,
                Check.outcome.is_(None),
                Check.archived_at.is_(None),
            )
            .order_by(Check.id)
        )
    )


async def check_card_ids(session: AsyncSession, check_id: int) -> list[int]:
    return sorted(
        await session.scalars(select(CardCheck.card_id).where(CardCheck.check_id == check_id))
    )


async def create_check(
    session: AsyncSession,
    *,
    title: str,
    note: str = "",
    repeatable: bool = False,
) -> Check:
    """Create one Pending Check, attached to nothing.

    Linking is a Card action: `create_card(check_ids=...)` or `toggle_card_check`. Keeping
    it out of here leaves exactly one write path for the link, so every attach lands in the
    Card's event log.
    """
    clean_title = title.strip()
    if not clean_title:
        raise DomainError("Check title cannot be empty")
    check = Check(
        title=clean_title,
        note=note.strip(),
        repeatable=repeatable,
    )
    session.add(check)
    await session.flush()
    check.series_id = check.id
    await _bump_workspace(session)
    return check


async def update_check_fields(
    session: AsyncSession, check_id: int, fields: dict[str, Any]
) -> Check:
    check = await session.get(Check, check_id)
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    unknown = set(fields) - {"title", "note", "repeatable"}
    if unknown:
        raise DomainError("Unsupported Check fields: " + ", ".join(sorted(unknown)))
    for name, value in fields.items():
        if name in {"title", "note"}:
            value = str(value).strip()
        if name == "title" and not value:
            raise DomainError("Check title cannot be empty")
        setattr(check, name, value)
    check.version += 1
    await _bump_workspace(session)
    return check


async def archive_check(session: AsyncSession, check_id: int, archive: bool = True) -> Check:
    check = await session.get(Check, check_id)
    if check is None:
        raise DomainError("Check does not exist")
    check.archived_at = utcnow() if archive else None
    check.version += 1
    await _bump_workspace(session)
    return check


async def toggle_card_check(
    session: AsyncSession, card_id: int, check_id: int, *, actor: ActorType = ActorType.USER_UI
) -> bool:
    """Toggle a direct Check link and return whether it is now linked.

    Shaped exactly like `toggle_card_value`: the link is a Card relationship, so it is
    written from the Card and recorded in that Card's event log.
    """
    card = await session.get(Card, card_id)
    check = await session.get(Check, check_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    link = await session.get(CardCheck, (card_id, check_id))
    before = card_snapshot(card)
    if link is None:
        session.add(CardCheck(card_id=card_id, check_id=check_id))
        operation, linked = "link_check", True
    else:
        await session.delete(link)
        operation, linked = "unlink_check", False
    card.version += 1
    check.version += 1
    await _record_event(session, card, operation, actor, before, new_correlation_id())
    await _bump_workspace(session)
    return linked


async def _copy_check(
    session: AsyncSession, source: Check, series_id: int, card_ids: Iterable[int]
) -> Check:
    successor = Check(
        title=source.title,
        note=source.note,
        repeatable=source.repeatable,
        series_id=series_id,
        source_instance_id=source.id,
    )
    session.add(successor)
    await session.flush()
    for card_id in sorted(set(card_ids)):
        session.add(CardCheck(card_id=card_id, check_id=successor.id))
    return successor


async def _spawn_check_successor(session: AsyncSession, check: Check) -> Check | None:
    linked_card_ids = await check_card_ids(session, check.id)
    eligible: list[int] = []
    for card_id in linked_card_ids:
        card = await session.get(Card, card_id)
        if card is None or card.archived_at is not None:
            continue
        if CardStage(card.effective_stage) in TERMINAL_STAGES:
            continue
        eligible.append(card_id)
    if linked_card_ids and not eligible:
        # A terminal or archived Card must never regain a Pending Check, or the Done-gate
        # would block it on every later reopen, and a repeatable Check would deadlock it.
        # A Check whose live Cards are all closed therefore ends its series here.
        return None
    series_id = check.series_id or check.id
    check.series_id = series_id
    live_in_series = await session.scalar(
        select(func.count())
        .select_from(Check)
        .where(
            Check.series_id == series_id,
            Check.outcome.is_(None),
            Check.archived_at.is_(None),
        )
    )
    if live_in_series:
        return None
    return await _copy_check(session, check, series_id, eligible)


async def _apply_check_outcome(
    session: AsyncSession,
    check: Check,
    outcome: CheckOutcome | str,
    actor: ActorType,
    *,
    spawn: bool,
) -> Check | None:
    resolved = CheckOutcome(outcome)
    was_pending = check.outcome is None
    check.outcome = resolved.value
    check.resolved_by = actor.value
    if was_pending:
        # resolved_at is the observation time the trend is keyed on, so a later
        # correction must not move the data point; updated_at carries that edit.
        check.resolved_at = utcnow()
    check.version += 1
    if not (was_pending and spawn and check.repeatable):
        return None
    return await _spawn_check_successor(session, check)


async def resolve_check(
    session: AsyncSession,
    check_id: int,
    outcome: CheckOutcome | str,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> tuple[Check, Check | None]:
    """Answer one Check; only the first answer spawns a repeatable successor."""
    check = await session.get(Check, check_id)
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    successor = await _apply_check_outcome(session, check, outcome, actor, spawn=True)
    await _bump_workspace(session)
    return check, successor


async def resolve_checks_for_card(
    session: AsyncSession,
    card_id: int,
    outcomes: dict[int, CheckOutcome | str] | None,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> list[Check]:
    """Answer every Pending Check on a Card so the Card can then be completed.

    Spawning is suppressed for the same reason it is in ``finish_action``: a repeatable
    Check would immediately put a new Pending row back on the Card and block it again.
    """
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    pending = await pending_checks(session, card_id)
    if not pending:
        raise DomainError("This Card has no Pending Checks")
    resolutions = _pending_check_resolutions(pending, outcomes)
    for check in pending:
        await _apply_check_outcome(session, check, resolutions[check.id], actor, spawn=False)
    await _bump_workspace(session)
    return pending


def _pending_check_resolutions(
    pending: list[Check], outcomes: dict[int, CheckOutcome | str] | None
) -> dict[int, CheckOutcome]:
    supplied = {int(key): CheckOutcome(value) for key, value in (outcomes or {}).items()}
    unknown = set(supplied) - {check.id for check in pending}
    if unknown:
        raise DomainError(
            "These Checks are not Pending on this Card: "
            + ", ".join(f"#{check_id}" for check_id in sorted(unknown))
        )
    missing = [check for check in pending if check.id not in supplied]
    if missing:
        raise DomainError(
            "Resolve these Pending Checks before finishing the Card: "
            + ", ".join(f"#{check.id} {check.title}" for check in missing)
        )
    return supplied


async def _clone_checks_for_successor(
    session: AsyncSession, card: Card, successor_id: int
) -> None:
    """Carry one Pending copy of each Check series onto a repeat successor.

    Grouping by series matters: a repeatable Check that already spawned inside this
    cycle leaves both the answered original and its live successor on the Card, and
    copying both would put two Pending rows of one series on the new Card.

    The copy is linked to the successor Card only. Cards this Check series is also
    linked to keep their own rows; the repeat belongs to the Card that repeated.
    """
    latest: dict[int, Check] = {}
    for check in await card_checks(session, card.id):
        latest[check.series_id or check.id] = check
    for series_id, check in sorted(latest.items()):
        await _copy_check(session, check, series_id, [successor_id])


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
    changed: list[int] = []
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
    if commitment is None:
        return
    if previous_stage in TERMINAL_STAGES and current not in TERMINAL_STAGES:
        # A reopened Action is no longer a completed or cancelled Sprint result.
        commitment.result = None
    if commitment.removed_at is not None and current in {CardStage.SPRINT, CardStage.TODAY}:
        # Returning to Sprint scope cancels the earlier removal instead of
        # counting the same effort as both removed and selected.
        commitment.removed_at = None


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
    if stage in TERMINAL_STAGES and card.kind == CardKind.ACTION.value:
        # finish_action owns completion timestamps, feedback, Sprint results and
        # repeat successors.  Moving an Action to a terminal stage here would set
        # only the stage and silently skip all of that accounting.
        raise DomainError("An Action reaches Done or Cancelled through finish_action")
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
    await _clone_checks_for_successor(session, card, successor.id)
    await _sync_commitment_for_stage(session, successor)
    return successor


async def finish_action(
    session: AsyncSession,
    card_id: int,
    terminal_stage: CardStage,
    *,
    actor: ActorType = ActorType.USER_UI,
    check_outcomes: dict[int, CheckOutcome | str] | None = None,
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
    # Done is gated on Pending Checks; Cancelled is not, because abandoning a Card
    # with unanswered Checks is legitimate.  Resolved before anything is mutated.
    pending = await pending_checks(session, card.id) if terminal_stage is CardStage.DONE else []
    resolutions = _pending_check_resolutions(pending, check_outcomes)
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
    # Suppressing the spawn is what breaks the deadlock: a repeatable Check would
    # otherwise put a fresh Pending row on the Card being closed.  The successor Card
    # above already carries the copies, so the series continues there instead.
    for check in pending:
        await _apply_check_outcome(session, check, resolutions[check.id], actor, spawn=False)
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
    # Numbering follows the highest number ever used, so deleting a Sprint cannot
    # produce a duplicate on the unique constraint.
    highest = await session.scalar(select(func.max(Sprint.number))) or 0
    start = start_date or date.today()
    sprint = Sprint(
        number=highest + 1,
        planned_start_date=start,
        planned_end_date=start + timedelta(days=SPRINT_LENGTH_DAYS - 1),
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


async def _linked_checks(session: AsyncSession, card_id: int) -> list[Check]:
    """Every Check on a Card, archived ones included, so a restore can revive them."""
    return list(
        await session.scalars(
            select(Check)
            .join(CardCheck, CardCheck.check_id == Check.id)
            .where(CardCheck.card_id == card_id)
            .order_by(Check.id)
        )
    )


async def _has_other_live_card(session: AsyncSession, check_id: int, card_id: int) -> bool:
    return bool(
        await session.scalar(
            select(func.count())
            .select_from(CardCheck)
            .join(Card, Card.id == CardCheck.card_id)
            .where(
                CardCheck.check_id == check_id,
                CardCheck.card_id != card_id,
                Card.archived_at.is_(None),
            )
        )
    )


async def archive_subtree(session: AsyncSession, card_id: int, archive: bool = True) -> list[int]:
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    changed: list[int] = []
    stamp = utcnow() if archive else None
    correlation_id = new_correlation_id()

    async def visit(node: Card) -> None:
        before = card_snapshot(node)
        node.archived_at = stamp
        node.version += 1
        for check in await _linked_checks(session, node.id):
            # A shared Check survives while any other live Card still needs it; the
            # subtree is archived node by node, so the last one carries it over.
            if archive and await _has_other_live_card(session, check.id, node.id):
                continue
            check.archived_at = stamp
            check.version += 1
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
    # A Check linked to a Card outside this subtree is still in use, so only Checks that
    # lose every link go with the Cards.  Both deletes are explicit rather than left to
    # the FK cascade, which is a connection pragma and not guaranteed here.
    await session.execute(
        delete(Check).where(
            Check.id.in_(select(CardCheck.check_id).where(CardCheck.card_id.in_(ids))),
            Check.id.not_in(select(CardCheck.check_id).where(CardCheck.card_id.not_in(ids))),
        )
    )
    await session.execute(delete(CardCheck).where(CardCheck.card_id.in_(ids)))
    await session.execute(delete(Card).where(Card.id.in_(ids)))
    await propagate_ancestors(session, parent_id)
    await _bump_workspace(session)
    return len(ids)


# Declared last so each spec can name the toggle command that writes it.
VALUE_REFERENCE = ReferenceSpec(
    "value_id", "value_ids", "value_query", Value, "Value", CardValue, toggle_card_value
)
TAG_REFERENCE = ReferenceSpec(
    "tag_id", "tag_ids", "tag_query", Tag, "Tag", CardTag, toggle_card_tag
)
# A Check is a Card relationship like the other two, so it resolves, diffs and applies
# through the same spec; only the name column differs.
CHECK_REFERENCE = ReferenceSpec(
    "check_id", "check_ids", "check_query", Check, "Check", CardCheck, toggle_card_check, "title"
)
CARD_REFERENCE_SPECS = (VALUE_REFERENCE, TAG_REFERENCE, CHECK_REFERENCE)
