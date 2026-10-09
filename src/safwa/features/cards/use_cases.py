"""Writing a Card: what one is, where it may sit, and the fields its kind may carry.

A Sprint commitment follows an Action's stage, and every writer of that stage is in this
file: each one calls Planning's door afterwards, and no Card row here ever touches a
commitment itself.

A Goal or Subgoal is closed explicitly here. Each operation ends at `propagate_ancestors`
in [hierarchy.py](hierarchy.py), which keeps its live stage and derived columns current.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError

from ...enums import ActorType
from ...foundation.log_events import CREATE, DELETE, UPDATE, record_log_event, snapshot
from ...foundation.workspace import bump_workspace
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
    refresh_schedule_commitment,
    sync_commitment_for_stage,
    sync_successor_commitment,
)
from ..schedules.api import (
    close_deleted_schedules,
    planned_executions,
    prepare_occurrence,
    scheduled_stage,
    set_schedule,
    successor_slot,
    workspace_zone,
)
from ..schedules.use_cases import drop_reminders, follow_remind
from ..tags.api import Tag, attach_tags, unlinkable_tag_id
from ..tags.model import CardTag
from ..values.api import Value, attach_values, unlinkable_value_id
from ..values.model import CardValue
from .hierarchy import branch_actions, card_children, propagate_ancestors, settle_archive
from .model import (
    CARD_TREE_DEPTH_MAX,
    EFFORT_POINTS,
    TERMINAL_STAGES,
    TRACKED_MINS_MAX,
    Card,
    CardCategory,
    CardCheck,
    CardEnergyType,
    CardKind,
    CardStage,
    Category,
    EnergyType,
    Priority,
    TodayDay,
    effort_label,
)

# The fields an Action alone carries. On a Goal and a Subgoal two of them are derived, so
# nothing outside `propagate_ancestors` may write one.
ACTION_ONLY_FIELDS = (
    "effort_points",
    "tracked_mins",
    "blocked_description",
)


@dataclass
class OperationResult:
    card_ids: list[int] = field(default_factory=list)
    ancestor_ids: list[int] = field(default_factory=list)
    successor_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def create_card(
    session: AsyncSession,
    *,
    kind: CardKind | str,
    title: str,
    note: str = "",
    stage: CardStage | str = CardStage.BACKLOG,
    priority: Priority | str = Priority.MEDIUM,
    schedule: str | None = None,
    schedule_rule: dict[str, Any] | None = None,
    blocked_description: str = "",
    effort_points: float | None = None,
    parent_id: int | None = None,
    categories: set[Category | str] | None = None,
    energy_types: set[EnergyType | str] | None = None,
    value_ids: set[int] | None = None,
    tag_ids: set[int] | None = None,
    check_ids: set[int] | None = None,
    actor: ActorType = ActorType.USER_UI,
) -> Card:
    """Create one reviewed Card through the same domain boundary used by UI and AI.

    A Goal's or Subgoal's Schedule is its Deadline. `schedule_rule` is the rule the
    Scheduler read `schedule` as. An Action with a `blocked_description` is blocked.
    """
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
        # A live stage belongs to an Action, like effort and Blocked: a parent shows what its
        # branch is in, so anything asked for here is dropped rather than refused.
        card_stage = CardStage.BACKLOG
        effort_points = None
        clean_description = ""
        category_values.clear()
        energy_values.clear()
    validate_action_fields(card_kind, effort_points, category_values, energy_values)
    await validate_parent(session, parent_id)

    if (loose := await unlinkable_value_id(session, value_ids or set())) is not None:
        raise DomainError(f"Value #{loose} does not exist")
    if (loose := await unlinkable_tag_id(session, tag_ids or set())) is not None:
        raise DomainError(f"Tag #{loose} does not exist")
    for check_id in check_ids or set():
        check = await session.get(Check, check_id)
        if check is None or check.archived_at is not None:
            raise DomainError(f"Check #{check_id} does not exist or is archived")
        if check.schedule:
            raise DomainError("A Check with its own Schedule must stay independent")
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
        blocked_description=clean_description,
        effort_points=effort_points,
    )
    session.add(card)
    await session.flush()
    await set_schedule(session, card, schedule, schedule_rule)
    for category in sorted(category_values):
        session.add(CardCategory(card_id=card.id, category=category))
    for energy_type in sorted(energy_values):
        session.add(CardEnergyType(card_id=card.id, energy_type=energy_type))
    await attach_values(session, card.id, value_ids or set())
    await attach_tags(session, card.id, tag_ids or set())
    for check_id in sorted(check_ids or set()):
        session.add(CardCheck(card_id=card.id, check_id=check_id))
    await record_card_event(session, card, CREATE, actor)
    record_change(session, CARD_CREATED, card.id)
    if card.blocked:
        record_change(session, CARD_BLOCKED, card.id)
    if card_kind is CardKind.ACTION:
        if card_stage is CardStage.TODAY:
            record_change(session, CARD_TODAY, card.id)
        await sync_commitment_for_stage(session, card)
    await propagate_ancestors(session, parent_id)
    await bump_workspace(session)
    return card


async def edit_card_text(session: AsyncSession, card_id: int, field: str, value: str) -> Card:
    if field not in {"title", "note"}:
        raise DomainError("Only a Card title or Note can be edited as text")
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    normalized = value.strip()
    if field == "title" and not normalized:
        raise DomainError("Card title cannot be empty")
    before = snapshot(card)
    setattr(card, field, normalized)
    card.version += 1
    await record_card_event(session, card, f"edit_{field}", ActorType.USER_UI, before)
    await follow_remind(session, card)
    await bump_workspace(session)
    return card


async def edit_card_schedule(
    session: AsyncSession, card_id: int, text: str | None, rule: dict[str, Any] | None
) -> Card:
    """Write a Schedule the owner typed, with the rule the editor compiled for it."""
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    before = snapshot(card)
    await _write_schedule(session, card, text, rule)
    card.version += 1
    await record_card_event(session, card, "edit_schedule", ActorType.USER_UI, before)
    await follow_remind(session, card)
    await bump_workspace(session)
    return card


async def _write_schedule(
    session: AsyncSession, card: Card, text: str | None, rule: dict[str, Any] | None
) -> None:
    await set_schedule(session, card, text, rule)
    await refresh_schedule_commitment(session, card)
    if rule and card.effective_stage == CardStage.TODAY.value:
        record_change(session, CARD_TODAY, card.id)


async def update_card_fields(
    session: AsyncSession,
    card_id: int,
    fields: dict[str, Any],
    *,
    actor: ActorType = ActorType.USER_UI,
) -> Card:
    """Apply validated editable Card fields through the domain/audit boundary.

    `schedule` comes with `schedule_rule`, the rule the Scheduler read it as. A
    `blocked_description` blocks an Action, and an empty one unblocks it."""
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    allowed = {
        "title",
        "note",
        "priority",
        "schedule",
        "schedule_rule",
        "blocked_description",
        "effort_points",
        "tracked_mins",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise DomainError("Unsupported Card fields: " + ", ".join(sorted(unknown)))
    if card.kind != CardKind.ACTION.value:
        # `effort_points` and `tracked_mins` on a parent are derived values this walk
        # writes, and a parent is never blocked; a caller that set one by hand would be
        # overwritten at the next Action change.
        for name in ACTION_ONLY_FIELDS:
            fields.pop(name, None)
        if not fields:
            raise DomainError("Goal and Subgoal cards cannot have Action-only fields")
    before = snapshot(card)
    for name, value in fields.items():
        if name in {"title", "note", "blocked_description"}:
            value = str(value or "").strip()
        if name == "title" and not value:
            raise DomainError("Card title cannot be empty")
        if name == "priority":
            value = Priority(value).value
        if name == "schedule":
            await _write_schedule(session, card, value, fields.get("schedule_rule"))
            continue
        if name == "schedule_rule":
            continue
        setattr(card, name, value)
    if card.kind == CardKind.ACTION.value:
        validate_action_fields(card.kind, card.effort_points)
        validate_tracked_mins(card.tracked_mins)
    card.version += 1
    await record_card_event(session, card, UPDATE, actor, before)
    await follow_remind(session, card, actor=actor)
    if card.blocked and not before["blocked_description"]:
        record_change(session, CARD_BLOCKED, card.id)
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
    before = snapshot(card)
    if link is None:
        session.add(CardValue(card_id=card_id, value_id=value_id))
        operation, linked = "link_value", True
    else:
        await session.delete(link)
        operation, linked = "unlink_value", False
    card.version += 1
    await record_card_event(session, card, operation, actor, before)
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
    before = snapshot(card)
    if link is None:
        session.add(CardTag(card_id=card_id, tag_id=tag_id))
        operation, linked = "link_tag", True
    else:
        await session.delete(link)
        operation, linked = "unlink_tag", False
    card.version += 1
    await record_card_event(session, card, operation, actor, before)
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
    before = snapshot(card)
    if link is None:
        session.add(CardCategory(card_id=card_id, category=category.value))
        operation, linked = "link_category", True
    else:
        await session.delete(link)
        operation, linked = "unlink_category", False
    card.version += 1
    await record_card_event(session, card, operation, actor, before)
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
    before = snapshot(card)
    if link is None:
        session.add(CardEnergyType(card_id=card_id, energy_type=energy_type.value))
        operation, linked = "link_energy", True
    else:
        await session.delete(link)
        operation, linked = "unlink_energy", False
    card.version += 1
    await record_card_event(session, card, operation, actor, before)
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
    if link is None and check.schedule:
        raise DomainError("A Check with its own Schedule must stay independent")
    if link is None and (held_by := await check_card_id(session, check_id)) is not None:
        raise DomainError(f"Check #{check_id} already belongs to Card #{held_by}")
    before = snapshot(card)
    if link is None:
        session.add(CardCheck(card_id=card_id, check_id=check_id))
        operation, linked = "link_check", True
    else:
        await session.delete(link)
        operation, linked = "unlink_check", False
    card.version += 1
    check.version += 1
    await record_card_event(session, card, operation, actor, before)
    await bump_workspace(session)
    return linked


async def set_card_parent(
    session: AsyncSession,
    card_id: int,
    parent_id: int | None,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> Card:
    """Move a branch, repairing both its previous and its new ancestors."""
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    await validate_parent(session, parent_id, card_id=card.id)
    if card.parent_id == parent_id:
        return card

    previous_parent_id = card.parent_id
    before = snapshot(card)
    card.parent_id = parent_id
    card.version += 1
    await record_card_event(session, card, "set_parent", actor, before)
    await propagate_ancestors(session, previous_parent_id)
    await propagate_ancestors(session, parent_id)
    await bump_workspace(session)
    return card


async def validate_parent(
    session: AsyncSession,
    parent_id: int | None,
    *,
    card_id: int | None = None,
) -> Card | None:
    if parent_id is None:
        return None
    parent = await session.get(Card, parent_id)
    if parent is None or parent.archived_at is not None:
        raise DomainError("Parent does not exist or is archived")
    if parent.kind == CardKind.ACTION.value:
        raise DomainError("An Action cannot have children")
    parent_depth = 0
    ancestor: Card | None = parent
    while ancestor is not None:
        if ancestor.id == card_id:
            raise DomainError("A Card cannot be placed under itself or its descendant")
        parent_depth += 1
        ancestor = await session.get(Card, ancestor.parent_id) if ancestor.parent_id else None

    async def branch_height(node_id: int) -> int:
        heights = [await branch_height(child.id) for child in await card_children(session, node_id)]
        return 1 + max(heights, default=0)

    height = await branch_height(card_id) if card_id is not None else 1
    if parent_depth + height > CARD_TREE_DEPTH_MAX:
        raise DomainError(f"A Card tree may have at most {CARD_TREE_DEPTH_MAX} levels, including Actions")
    return parent


def validate_action_fields(
    kind: CardKind | str,
    effort_points: float | None,
    categories: set[str] | None = None,
    energy_types: set[str] | None = None,
) -> None:
    kind = CardKind(kind)
    if kind is CardKind.ACTION:
        if effort_points is not None and (isinstance(effort_points, bool) or effort_points not in EFFORT_POINTS):
            raise DomainError(
                "An Action's effort points must be one of: "
                + ", ".join(effort_label(rung) for rung in sorted(EFFORT_POINTS))
            )
        return
    if effort_points is not None or categories or energy_types:
        raise DomainError("Goal and Subgoal cards cannot have Action-only fields")


def validate_tracked_mins(minutes: int | None) -> None:
    """None, or a whole number of minutes an Action may have taken."""
    if minutes is None:
        return
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise DomainError("The time an Action took is a whole number of minutes")
    if not 1 <= minutes <= TRACKED_MINS_MAX:
        raise DomainError(f"The time an Action took is 1 to {TRACKED_MINS_MAX} minutes")


# The changes a hook may follow up on: a Card was created, an Action became blocked, entered
# Today or was finished, however it was saved; and one stood in Today when the morning came.
# A repeating Action's next instance is the system's, so it is created without `CARD_CREATED`.
CARD_CREATED = "card.created"
CARD_BLOCKED = "card.blocked"
CARD_TODAY = "card.today"
CARD_DONE = "card.done"
CARD_ACTIONS_FINISHED = "card.actions_finished"
CARD_TODAY_MORNING = "card.today_morning"


async def record_today_morning(
    session: AsyncSession, *, now: datetime | None = None
) -> list[Card]:
    """Write the local day down for each open Action in Today, once per morning.

    A second look the same morning writes nothing and hands nothing on.
    """
    tz = await workspace_zone(session)
    day = (now or utcnow()).astimezone(tz).date()
    in_today = await session.scalars(
        select(Card)
        .where(
            Card.kind == CardKind.ACTION.value,
            Card.effective_stage == CardStage.TODAY.value,
            Card.archived_at.is_(None),
        )
        .order_by(Card.id)
    )
    written = set(await session.scalars(select(func.coalesce(Card.repeat_series_id, Card.id))
                                       .join(TodayDay, TodayDay.card_id == Card.id).where(TodayDay.day == day)))
    found = [card for card in in_today if (card.repeat_series_id or card.id) not in written]
    counts = await planned_executions(session, found, day, day)
    for card in found:
        session.add(TodayDay(card_id=card.id, day=day, planned_count=counts[card.id]))
        record_change(session, CARD_TODAY_MORNING, card.id)
    return found


async def record_card_event(
    session: AsyncSession,
    card: Card,
    operation: str,
    actor: ActorType,
    before: dict[str, Any] | None = None,
) -> None:
    await record_log_event(session, "card", card, card.title, operation, actor, before)


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
    before = snapshot(card)
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
    await record_card_event(session, card, "move", actor, before)
    if stage is CardStage.TODAY and previous is not CardStage.TODAY:
        record_change(session, CARD_TODAY, card.id)
    await sync_commitment_for_stage(session, card, previous)
    result.ancestor_ids = await propagate_ancestors(session, card.parent_id)
    await bump_workspace(session)
    return result


async def _copy_repeat_successor(
    session: AsyncSession, card: Card, live_stage: CardStage, slot: datetime | None
) -> Card:
    series_id = card.repeat_series_id or card.id
    card.repeat_series_id = series_id
    live_stage = CardStage(await scheduled_stage(session, card, slot, live_stage.value))
    successor = Card(
        parent_id=card.parent_id,
        kind=card.kind,
        title=card.title,
        note=card.note,
        manual_stage=live_stage.value,
        effective_stage=live_stage.value,
        priority=card.priority,
        schedule=card.schedule,
        schedule_record=card.schedule_record,
        period_start=slot,
        blocked_description=card.blocked_description,
        effort_points=card.effort_points,
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
    if live_stage is CardStage.TODAY:
        record_change(session, CARD_TODAY, successor.id)
    await sync_successor_commitment(session, card, successor)
    return successor


async def finish_action(
    session: AsyncSession,
    card_id: int,
    *,
    actor: ActorType = ActorType.USER_UI,
    check_outcomes: dict[int, Any] | None = None,
    tracked_mins: int | None = None,
) -> OperationResult:
    """Finish one Action; the minutes it took, when given, are written with it."""
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    if card.kind != CardKind.ACTION.value:
        raise DomainError("Only Actions are finished directly")
    if CardStage(card.effective_stage) in TERMINAL_STAGES:
        raise DomainError("Action is already terminal")
    validate_tracked_mins(tracked_mins)
    # Asked before anything is written, so a refusal leaves the Action where it was.
    answers = await require_check_answers(session, card.id, check_outcomes)
    await prepare_occurrence(session, card)
    previous_live_stage = CardStage(card.effective_stage)
    before = snapshot(card)
    card.manual_stage = CardStage.DONE.value
    card.effective_stage = CardStage.DONE.value
    card.completed_at = utcnow()
    if tracked_mins is not None:
        card.tracked_mins = tracked_mins
    card.version += 1
    await record_card_event(session, card, CardStage.DONE.value, actor, before)
    await record_sprint_result(session, card.id)
    record_change(session, CARD_DONE, card.id)
    result = OperationResult(card_ids=[card.id])
    if card.blocked:
        result.warnings.append(f"Blocked: {card.blocked_description}")
    await settle_checks(session, answers, actor=actor)
    repeats, slot = await successor_slot(session, card)
    successor = (
        await _copy_repeat_successor(session, card, previous_live_stage, slot) if repeats else None
    )
    if successor is not None:
        result.successor_ids.append(successor.id)
    await follow_remind(session, card, successor, actor=actor)
    result.ancestor_ids = await propagate_ancestors(session, card.parent_id)
    parent_id = card.parent_id
    while parent_id:
        parent = await session.get(Card, parent_id)
        if parent is None:
            break
        actions = await branch_actions(session, parent.id)
        if any(action.effective_stage != CardStage.DONE.value for action in actions):
            break
        if parent.effective_stage != CardStage.DONE.value:
            record_change(session, CARD_ACTIONS_FINISHED, parent.id)
        parent_id = parent.parent_id
    await bump_workspace(session)
    return result


async def require_finished_actions(session: AsyncSession, card_id: int) -> None:
    if any(
        action.effective_stage != CardStage.DONE.value
        for action in await branch_actions(session, card_id)
    ):
        raise DomainError("Finish the open Actions before closing their Goal or Subgoal")


async def finish_card(
    session: AsyncSession,
    card_id: int,
    *,
    actor: ActorType = ActorType.USER_UI,
    check_outcomes: dict[int, Any] | None = None,
    tracked_mins: int | None = None,
) -> OperationResult:
    """Close a Card explicitly, without closing any children."""
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    if card.kind == CardKind.ACTION.value:
        return await finish_action(
            session, card_id, actor=actor, check_outcomes=check_outcomes,
            tracked_mins=tracked_mins,
        )
    if card.effective_stage == CardStage.DONE.value:
        raise DomainError("Card is already terminal")
    if tracked_mins is not None:
        raise DomainError("Only an Action carries time spent")
    await require_finished_actions(session, card.id)
    answers = await require_check_answers(session, card.id, check_outcomes)
    before = snapshot(card)
    card.manual_stage = CardStage.DONE.value
    card.effective_stage = CardStage.DONE.value
    card.completed_at = utcnow()
    card.version += 1
    await record_card_event(session, card, CardStage.DONE.value, actor, before)
    await settle_checks(session, answers, actor=actor)
    await follow_remind(session, card, actor=actor)
    ancestors = await propagate_ancestors(session, card.id)
    await bump_workspace(session)
    return OperationResult(card_ids=[card.id], ancestor_ids=ancestors)


async def reopen_card(
    session: AsyncSession,
    card_id: int,
    stage: CardStage = CardStage.BACKLOG,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> OperationResult:
    """Reopen a parent independently; an Action returns to its chosen live stage."""
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    if card.kind == CardKind.ACTION.value:
        return await move_card(session, card_id, stage, actor=actor)
    if stage is not CardStage.BACKLOG:
        raise DomainError("A reopened Goal or Subgoal derives its live stage")
    if card.effective_stage != CardStage.DONE.value:
        raise DomainError("Card is already open")
    before = snapshot(card)
    card.manual_stage = CardStage.BACKLOG.value
    card.effective_stage = CardStage.BACKLOG.value
    card.completed_at = None
    card.archived_at = None
    card.version += 1
    await reopen_checks(session, card.id)
    await record_card_event(session, card, "reopen", actor, before)
    ancestors = await propagate_ancestors(session, card.id)
    await bump_workspace(session)
    return OperationResult(card_ids=[card.id], ancestor_ids=ancestors)


async def archive_subtree(
    session: AsyncSession,
    card_id: int,
    archive: bool = True,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> list[int]:
    """Archive or restore a branch by its Actions; a Goal and a Subgoal follow from theirs."""
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    if archive and CardStage(card.effective_stage) not in TERMINAL_STAGES:
        raise DomainError("Only a Card that is Done may be archived")
    stamp = utcnow() if archive else None
    actions = (
        [card] if card.kind == CardKind.ACTION.value else await branch_actions(session, card.id)
    )
    for action in actions:
        before = snapshot(action)
        action.archived_at = stamp
        action.version += 1
        await record_card_event(
            session, action, "archive" if archive else "restore", actor, before
        )
    changed = await settle_archive(session, actions)
    await bump_workspace(session)
    return changed


async def _purge_cards(session: AsyncSession, ids: list[int], actor: ActorType) -> None:
    # Every link and commitment is deleted by name rather than left to the FK cascade,
    # which is a connection pragma and not guaranteed here. The events stay, and each
    # Card's deletion is one more.
    await close_deleted_schedules(session, Card, ids)
    await drop_reminders(session, Card, ids, actor=actor)
    for card in await session.scalars(select(Card).where(Card.id.in_(ids))):
        await record_card_event(session, card, DELETE, actor, snapshot(card))
    await delete_checks_of_cards(session, ids)
    await delete_commitments_of_cards(session, ids)
    for model in (CardValue, CardTag, CardCategory, CardEnergyType, TodayDay):
        await session.execute(delete(model).where(model.card_id.in_(ids)))
    await session.execute(delete(Card).where(Card.id.in_(ids)))


async def delete_subtree(
    session: AsyncSession, card_id: int, *, actor: ActorType = ActorType.USER_UI
) -> int:
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
    await _purge_cards(session, ids, actor)
    await propagate_ancestors(session, parent_id)
    await bump_workspace(session)
    return len(ids)


async def delete_one_card(
    session: AsyncSession, card_id: int, *, actor: ActorType = ActorType.USER_UI
) -> int:
    """Delete one Card, giving its children its parent or leaving them at the root."""
    card = await session.get(Card, card_id)
    if card is None:
        raise DomainError("Card does not exist")
    parent_id = card.parent_id
    for child in await session.scalars(select(Card).where(Card.parent_id == card.id)):
        before = snapshot(child)
        child.parent_id = parent_id
        child.version += 1
        await record_card_event(session, child, "set_parent", actor, before)
    await _purge_cards(session, [card_id], actor)
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
