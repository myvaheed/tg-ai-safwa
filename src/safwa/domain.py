from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .constants import (
    ARCHIVE_MARKER,
    REPEAT_LIVE,
    REPEAT_MARKER,
)
from .enums import (
    ActorType,
    CardKind,
    Category,
    EnergyType,
)
from .features.cards.model import TERMINAL_STAGES
from .features.cards.use_cases import OperationResult as OperationResult
from .features.cards.use_cases import aggregate_child_stages as aggregate_child_stages
from .features.cards.use_cases import archive_settled_cards, record_card_event
from .features.cards.use_cases import archive_subtree as archive_subtree
from .features.cards.use_cases import blocking_actions as blocking_actions
from .features.cards.use_cases import branch_actions as branch_actions
from .features.cards.use_cases import card_children as card_children
from .features.cards.use_cases import card_progress as card_progress
from .features.cards.use_cases import card_snapshot as card_snapshot
from .features.cards.use_cases import create_card as create_card
from .features.cards.use_cases import delete_subtree as delete_subtree
from .features.cards.use_cases import edit_card_text as edit_card_text
from .features.cards.use_cases import finish_action as finish_action
from .features.cards.use_cases import is_closed_repeat as _is_closed_repeat_card
from .features.cards.use_cases import move_card as move_card
from .features.cards.use_cases import propagate_ancestors as propagate_ancestors
from .features.cards.use_cases import set_card_parent as set_card_parent
from .features.cards.use_cases import update_card_fields as update_card_fields
from .features.cards.use_cases import validate_action_fields as validate_action_fields
from .features.cards.use_cases import validate_blocked_fields as validate_blocked_fields
from .features.cards.use_cases import validate_parent as validate_parent
from .features.checks.model import CheckOutcome as CheckOutcome
from .features.checks.use_cases import archive_check as archive_check
from .features.checks.use_cases import archive_settled_checks
from .features.checks.use_cases import card_checks as card_checks
from .features.checks.use_cases import check_card_id as check_card_id
from .features.checks.use_cases import check_value_ids as check_value_ids
from .features.checks.use_cases import create_check as create_check
from .features.checks.use_cases import delete_check as delete_check
from .features.checks.use_cases import is_closed_repeat as _is_closed_repeat_check
from .features.checks.use_cases import pending_checks as pending_checks
from .features.checks.use_cases import resolve_check as resolve_check
from .features.checks.use_cases import unobserved_series as unobserved_series
from .features.checks.use_cases import update_check_fields as update_check_fields
from .features.planning.api import settled_cutoff
from .features.planning.api import sync_commitment_for_stage as sync_commitment_for_stage
from .features.planning.use_cases import finish_sprint as _finish_sprint
from .features.planning.use_cases import set_sprint_success_criteria as set_sprint_success_criteria
from .features.planning.use_cases import sprint_is_due
from .features.planning.use_cases import sprint_length_days as sprint_length_days
from .features.planning.use_cases import sprint_metrics as sprint_metrics
from .features.planning.use_cases import sprint_summary as sprint_summary
from .features.planning.use_cases import start_sprint as start_sprint
from .features.tags.use_cases import create_tag as create_tag
from .features.tags.use_cases import delete_tag as delete_tag
from .features.tags.use_cases import update_tag_fields as update_tag_fields
from .features.values.use_cases import create_value as create_value
from .features.values.use_cases import delete_value as delete_value
from .features.values.use_cases import set_value_focus as set_value_focus
from .features.values.use_cases import update_value_fields as update_value_fields
from .features.values.use_cases import value_link_counts as value_link_counts
from .foundation.clock import utcnow as utcnow
from .foundation.errors import DomainError
from .foundation.errors import StaleStateError as StaleStateError
from .foundation.workspace import bump_workspace as _bump_workspace
from .models import (
    Card,
    CardCategory,
    CardCheck,
    CardEnergyType,
    CardTag,
    CardValue,
    Check,
    CheckValue,
    Sprint,
    Tag,
    UserProfile,
    Value,
    Workspace,
    new_correlation_id,
)


def listed(value: Any) -> list[Any]:
    """Accept either one reference or a list of them from a proposal payload."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@dataclass(frozen=True)
class ReferenceSpec:
    """One Card relationship: where it lives in a payload and how it is written.

    ``singular_key`` doubles as the link table's own column name, so the same spec
    addresses the payload, the lookup and the junction row.  ``owner_key`` is the other
    half of that row: a Card carries Values, Tags and Checks, and a Check carries Values.
    """

    singular_key: str
    plural_key: str
    query_key: str
    model: type[Tag] | type[Value] | type[Check]
    label: str
    link_model: type[CardTag] | type[CardValue] | type[CardCheck] | type[CheckValue]
    toggle: Callable[..., Awaitable[bool]]
    # A Check is named by `title`, so the column a query_key resolves against varies.
    name_attr: str = "name"
    owner_key: str = "card_id"
    # What does the linking, for a screen that counts what carries this item.
    owner_label: str = "Card"
    # Other link tables that can carry the same item, so a screen counts them all without
    # knowing which item it is looking at.
    also_carried_by: tuple[ReferenceSpec, ...] = ()
    # Only a Check is ever archived; a Value and a Tag are deleted instead, so there is no
    # archived one for a link to be refused against.
    archivable: bool = False

    def is_live(self, entity: Any) -> bool:
        return not (self.archivable and entity.archived_at is not None)

    @property
    def live_filters(self) -> tuple[Any, ...]:
        return (self.model.archived_at.is_(None),) if self.archivable else ()

    def mentioned_in(self, values: dict[str, Any]) -> bool:
        return bool({self.singular_key, self.plural_key, self.query_key} & values.keys())

    def link_key(self, owner_id: int, entity_id: int) -> dict[str, int]:
        return {self.owner_key: owner_id, self.singular_key: entity_id}

    @property
    def link_column(self) -> Any:
        return self.link_model.__table__.c[self.singular_key]

    @property
    def owner_column(self) -> Any:
        return self.link_model.__table__.c[self.owner_key]

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
        if entity is None or not spec.is_live(entity):
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
                    *spec.live_filters,
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
    await _bump_workspace(session)
    return linked


async def toggle_check_value(
    session: AsyncSession, check_id: int, value_id: int, *, actor: ActorType = ActorType.USER_UI
) -> bool:
    """Put a Value on a Check or take it off, and say whether it is on now.

    Nothing is recorded against the Cards that Check belongs to: a Check's Values are its
    own statement about what it measures, and their Values are theirs.
    """
    del actor  # a Check keeps no event log of its own
    check = await session.get(Check, check_id)
    value = await session.get(Value, value_id)
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    if value is None:
        raise DomainError("Value does not exist")
    link = await session.get(CheckValue, {"check_id": check_id, "value_id": value_id})
    if link is None:
        session.add(CheckValue(check_id=check_id, value_id=value_id))
        linked = True
    else:
        await session.delete(link)
        linked = False
    check.version += 1
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
    await record_card_event(session, card, operation, actor, before, new_correlation_id())
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
    await record_card_event(session, card, operation, actor, before, new_correlation_id())
    await _bump_workspace(session)
    return linked


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
    await _bump_workspace(session)
    return linked


def is_closed_repeat(entity: Card | Check) -> bool:
    """A repeat instance that already ended, so its series continues on a newer row.

    Editing one is almost always aimed at the live instance instead, and the edit
    would not reach it: the successor was copied at close time.
    """
    if isinstance(entity, Check):
        return _is_closed_repeat_check(entity)
    return _is_closed_repeat_card(entity)


async def title_marks(session: AsyncSession, entity: Card | Check) -> str:
    """What a title carries after it: its place in a repeat series, and the archive.

    The place is counted over every row the series has ever had, so archiving one does not
    renumber the others, and `live #7` is the open one — a closed instance read as the one
    to work with is the mistake both marks exist to stop.  Nothing is stored renamed:
    `ai_cards` and `ai_checks` render the same marks in SQL.
    """
    marks = ""
    if is_closed_repeat(entity):
        if isinstance(entity, Check):
            statement = (
                select(func.count())
                .select_from(Check)
                .where(Check.series_id == (entity.series_id or entity.id), Check.id <= entity.id)
            )
        else:
            statement = (
                select(func.count())
                .select_from(Card)
                .where(
                    Card.repeat_series_id == (entity.repeat_series_id or entity.id),
                    Card.id <= entity.id,
                )
            )
        live_id = await live_repeat_instance_id(session, entity)
        marks += REPEAT_MARKER.format(
            index=await session.scalar(statement),
            live="" if live_id is None else REPEAT_LIVE.format(live_id=live_id),
        )
    if entity.archived_at is not None:
        marks += ARCHIVE_MARKER
    return marks


async def live_repeat_instance_id(session: AsyncSession, entity: Card | Check) -> int | None:
    """The one open row of a repeat series, or None when the series has ended.

    Only the newest instance can be open, because closing one is what creates the next.
    """
    if isinstance(entity, Check):
        statement = (
            select(Check.id)
            .where(
                Check.series_id == (entity.series_id or entity.id),
                Check.outcome.is_(None),
                Check.archived_at.is_(None),
            )
            .order_by(Check.id.desc())
        )
    else:
        statement = (
            select(Card.id)
            .where(
                Card.repeat_series_id == (entity.repeat_series_id or entity.id),
                Card.effective_stage.notin_([stage.value for stage in TERMINAL_STAGES]),
                Card.archived_at.is_(None),
            )
            .order_by(Card.id.desc())
        )
    return await session.scalar(statement.limit(1))


async def archive_settled_items(session: AsyncSession) -> tuple[list[int], list[int]]:
    """Take what closed two Sprints ago off the screens, and report what left.

    A Sprint ending is the clock: nothing is archived while the workspace is in Planning,
    and whatever built up there leaves the moment the next Sprint ends.  Composed here
    because the clock is Planning's and the two archivers are Cards' and Checks'.
    """
    cutoff = await settled_cutoff(session)
    if cutoff is None:
        return [], []
    return (
        await archive_settled_cards(session, cutoff),
        await archive_settled_checks(session, cutoff),
    )


async def finish_sprint(session: AsyncSession, *, reason: str = "finished") -> Sprint:
    """End the Sprint and sweep what its ending settled."""
    sprint = await _finish_sprint(session, reason=reason)
    await archive_settled_items(session)
    return sprint


async def expire_due_sprint(session: AsyncSession, *, now: datetime | None = None) -> Sprint | None:
    """Close the active Sprint once local midnight has passed its planned end date.

    Unfinished Actions keep their stage: the Sprint ends, the plan does not evaporate.
    """
    if await sprint_is_due(session, now=now) is None:
        return None
    return await finish_sprint(session, reason="expired")


# Declared last so each spec can name the toggle command that writes it.
# The one link a Check carries itself.  Same shape, different side of the junction row.
CHECK_VALUE_REFERENCE = ReferenceSpec(
    "value_id",
    "value_ids",
    "value_query",
    Value,
    "Value",
    CheckValue,
    toggle_check_value,
    owner_key="check_id",
    owner_label="Check",
)
VALUE_REFERENCE = ReferenceSpec(
    "value_id",
    "value_ids",
    "value_query",
    Value,
    "Value",
    CardValue,
    toggle_card_value,
    also_carried_by=(CHECK_VALUE_REFERENCE,),
)
TAG_REFERENCE = ReferenceSpec(
    "tag_id", "tag_ids", "tag_query", Tag, "Tag", CardTag, toggle_card_tag
)
# A Check is a Card relationship like the other two, so it resolves, diffs and applies
# through the same spec; only the name column differs.
CHECK_REFERENCE = ReferenceSpec(
    "check_id",
    "check_ids",
    "check_query",
    Check,
    "Check",
    CardCheck,
    toggle_card_check,
    "title",
    archivable=True,
)
CARD_REFERENCE_SPECS = (VALUE_REFERENCE, TAG_REFERENCE, CHECK_REFERENCE)
