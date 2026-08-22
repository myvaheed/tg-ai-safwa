"""How a proposed Card, Check, Value or Tag is checked and then written.

Preparation runs against live data and writes nothing, so a failure is one model-visible
retryable tool error. Application calls the same domain operations the manual UI calls:
two paths, one operation.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.sql import RequestQueryError, UnsafeQueryError, normalize_request_sql
from ...domain import (
    CARD_REFERENCE_SPECS,
    CHECK_REFERENCE,
    CHECK_VALUE_REFERENCE,
    TAG_REFERENCE,
    VALUE_REFERENCE,
    DomainError,
    ReferenceSpec,
    StaleStateError,
    archive_check,
    archive_subtree,
    archive_tag,
    archive_value,
    create_card,
    create_check,
    create_tag,
    create_value,
    delete_subtree,
    finish_action,
    is_closed_repeat,
    live_repeat_instance_id,
    move_card,
    pending_checks,
    resolve_check,
    resolve_references,
    set_card_parent,
    toggle_card_category,
    toggle_card_energy_type,
    update_card_fields,
    update_check_fields,
    update_tag_fields,
    update_value_fields,
)
from ...enums import (
    CHECK_ANSWER_ACTIONS,
    TERMINAL_STAGES,
    ActorType,
    CardKind,
    CardStage,
    Category,
    EnergyType,
)
from ...models import Card, CardCategory, CardEnergyType, Check, ProposalChange, Tag, Value
from ..proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    ToolPreparationError,
    require_target,
)

REFERENCE_HINT = (
    "Find the item with query_safwa and retry this call with its numeric ID. If you "
    "proposed it earlier in this same turn, wait for that result and use the ID it returns."
)
PARENT_HINT = (
    "Find the parent with query_safwa and retry with its numeric parent_id, or drop the "
    "parent. If you proposed it earlier in this same turn, wait for that result first."
)
ACTION_ONLY_FIELDS = ("effort_points", "repeatable", "categories", "energy_types")
CARD_SCALAR_FIELDS = frozenset(
    {
        "title",
        "note",
        "priority",
        "hard_time",
        "blocked",
        "blocked_description",
        "effort_points",
        "repeatable",
    }
)


async def live_instance_hint(session: AsyncSession, entity: Card | Check) -> str:
    live_id = await live_repeat_instance_id(session, entity)
    if live_id is None:
        return "The series has ended. Tell the owner instead of proposing again."
    return f"Retry this call with #{live_id}, the open one in its series."


async def reject_closed_repeat(session: AsyncSession, entity: Card | Check, label: str) -> None:
    if not is_closed_repeat(entity):
        return
    raise ToolPreparationError(
        "closed_repeat",
        f"{label.title()} #{entity.id} is a closed repeat and cannot be changed.",
        await live_instance_hint(session, entity),
    )


def allows_parent(child_kind: str | None, parent_kind: str | None) -> bool:
    if child_kind == CardKind.IDEA.value:
        return parent_kind == CardKind.GOAL.value
    if child_kind == CardKind.ACTION.value:
        return parent_kind in {CardKind.GOAL.value, CardKind.IDEA.value}
    return False


async def _validate_named_references(
    session: AsyncSession, values: dict[str, Any], spec: ReferenceSpec
) -> None:
    """Reject a relationship the owner could not act on, with a retryable hint."""
    resolved = await resolve_references(session, spec, values)
    if resolved.blank:
        raise ToolPreparationError(
            "invalid_arguments",
            f"{spec.label} name must not be empty.",
            f"Provide one exact {spec.label} name or its numeric ID.",
        )
    if resolved.unknown_ids:
        raise ToolPreparationError(
            "reference_not_found",
            f"{spec.label} #{resolved.unknown_ids[0]} does not exist or is archived.",
            REFERENCE_HINT,
        )
    if resolved.missing:
        raise ToolPreparationError(
            "reference_not_found",
            f"{spec.label} '{resolved.missing[0]}' was not found.",
            REFERENCE_HINT,
        )
    if resolved.ambiguous:
        raise ToolPreparationError(
            "reference_ambiguous",
            f"{spec.label} '{resolved.ambiguous[0]}' matched more than one item.",
            f"Use query_safwa to choose one {spec.label} and retry with its numeric ID.",
        )
    if spec.model is not Check:
        return
    for check_id in sorted(resolved.ids):
        check = await session.get(Check, check_id)
        if check is not None and is_closed_repeat(check):
            raise ToolPreparationError(
                "closed_repeat",
                f"Check #{check_id} is a closed repeat and cannot be linked.",
                await live_instance_hint(session, check),
            )


async def _resolve_parent_reference(
    context: PreparationContext, values: dict[str, Any], child_kind: str | None
) -> None:
    session = context.session
    raw_parent_query = values.pop("parent_query", None)
    if raw_parent_query is not None:
        parent_query = str(raw_parent_query).strip()
        if not parent_query:
            raise ToolPreparationError(
                "invalid_arguments",
                "Parent query must not be empty.",
                "Provide one exact Card title, one numeric parent_id, or a safe SELECT returning id.",
            )
        if parent_query.casefold().startswith(("select", "with")):
            try:
                # Only the rows matter here; a parent query must match exactly one Card,
                # so a capped result is reported as ambiguous rather than silently used.
                rows = (
                    await context.query_runner.run(
                        normalize_request_sql(parent_query, context.views)
                    )
                ).rows
            except (RequestQueryError, UnsafeQueryError) as error:
                raise ToolPreparationError(
                    "unsafe_query",
                    f"Invalid parent query: {error}",
                    "Use one read-only SELECT over ai_cards that returns only the id column.",
                ) from error
            except (sqlite3.Error, TimeoutError) as error:
                raise ToolPreparationError(
                    "invalid_arguments",
                    f"Parent query failed: {error}",
                    "Fix the SELECT, or give parent_id or an exact Card title instead.",
                ) from error
            if not rows:
                raise ToolPreparationError(
                    "reference_not_found",
                    "The parent query returned no Cards.",
                    PARENT_HINT,
                )
            if len(rows) > 1:
                raise ToolPreparationError(
                    "reference_ambiguous",
                    "The parent query returned more than one Card.",
                    "Narrow the query to one Card and retry with its numeric ID.",
                )
            if set(rows[0]) != {"id"} or not isinstance(rows[0]["id"], int):
                raise ToolPreparationError(
                    "invalid_arguments",
                    "The parent query must return exactly one integer id column.",
                    "Use SELECT id FROM ai_cards ... and make it match one Card.",
                )
            values["parent_id"] = rows[0]["id"]
        else:
            matches = list(
                await session.scalars(
                    select(Card).where(
                        Card.title.collate("NOCASE") == parent_query,
                        Card.archived_at.is_(None),
                    )
                )
            )
            if not matches:
                raise ToolPreparationError(
                    "reference_not_found",
                    f"Parent Card '{parent_query}' was not found.",
                    PARENT_HINT,
                )
            if len(matches) > 1:
                raise ToolPreparationError(
                    "reference_ambiguous",
                    f"Parent Card '{parent_query}' matched more than one Card.",
                    "Use query_safwa to choose one parent and retry with its numeric ID.",
                )
            values["parent_id"] = matches[0].id

    parent_id = values.get("parent_id")
    if parent_id is None:
        return
    parent = await session.get(Card, int(parent_id))
    if parent is None or parent.archived_at is not None:
        raise ToolPreparationError(
            "reference_not_found",
            f"Parent Card #{parent_id} does not exist or is archived.",
            PARENT_HINT,
        )
    if not allows_parent(child_kind, parent.kind):
        raise ToolPreparationError(
            "invalid_parent_kind",
            f"A {child_kind or 'Card'} cannot have a {parent.kind} parent.",
            "Choose a parent this Card may hang under, or drop the parent and leave it root-level.",
        )


async def _guard_pending_checks(
    session: AsyncSession, change: Any, values: dict[str, Any]
) -> None:
    """Refuse to prepare a completion while the Card still has Pending Checks.

    The error is model-visible and retryable, and it carries the titles so the model
    does not have to spend a `query_safwa` round discovering them.
    """
    completing = change.action == "complete" or (
        change.action in {"move", "update"} and values.get("stage") == CardStage.DONE.value
    )
    if not completing or change.id is None:
        return
    pending = await pending_checks(session, int(change.id))
    if not pending:
        return
    listed_checks = ", ".join(f"#{check.id} “{check.title}”" for check in pending)
    raise ToolPreparationError(
        "pending_checks",
        f"Card #{change.id} still has Pending Checks: {listed_checks}.",
        "Answer each one first: check(mode='complete'|'cancel', id=…) when the user already "
        "said how it went, otherwise cite them as [title](check:<id>) so they answer them "
        "themselves. Then retry only this unfinished completion.",
    )


async def _named_ids(
    session: AsyncSession, values: dict[str, Any], spec: ReferenceSpec
) -> set[int]:
    """Resolve one relationship at approval time against committed data.

    A proposal holds one change, so a name referenced here always belongs to an
    item an earlier proposal already saved.
    """
    resolved = await resolve_references(session, spec, values)
    if resolved.unresolved:
        raise DomainError(
            f"{spec.label} '{resolved.unresolved[0]}' is not available for this approved link"
        )
    # Unknown numeric IDs stay for the domain command to reject with its own message.
    return resolved.ids | set(resolved.unknown_ids)


async def _apply_stage_change(session: AsyncSession, card: Card, stage: CardStage) -> None:
    """Route one approved stage change so terminal stages keep their accounting.

    ``finish_action`` owns completion timestamps, feedback, Sprint results and repeat
    successors; ``move_card`` owns live stages and subtree propagation.  Every approved
    stage change goes through here so no path can reach Done or Cancelled without the
    completion bookkeeping.
    """
    if stage in TERMINAL_STAGES:
        await finish_action(session, card.id, stage, actor=ActorType.AI)
        return
    await move_card(session, card.id, stage, actor=ActorType.AI)


async def _replace_card_sets(
    session: AsyncSession, card: Card, values: dict[str, Any]
) -> None:
    if "categories" in values:
        current = set(
            await session.scalars(
                select(CardCategory.category).where(CardCategory.card_id == card.id)
            )
        )
        target = set(values["categories"] or [])
        for category in sorted(current ^ target):
            await toggle_card_category(session, card.id, Category(category), actor=ActorType.AI)
    if "energy_types" in values:
        current = set(
            await session.scalars(
                select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
            )
        )
        target = set(values["energy_types"] or [])
        for energy_type in sorted(current ^ target):
            await toggle_card_energy_type(
                session, card.id, EnergyType(energy_type), actor=ActorType.AI
            )

    for spec in CARD_REFERENCE_SPECS:
        if not spec.mentioned_in(values):
            continue
        current = set(
            await session.scalars(
                select(spec.link_column).where(spec.link_model.card_id == card.id)
            )
        )
        target = await _named_ids(session, values, spec)
        for entity_id in sorted(current ^ target):
            await spec.toggle(session, card.id, entity_id, actor=ActorType.AI)


async def _apply_card_links(
    session: AsyncSession, card: Card, values: dict[str, Any], *, linked: bool
) -> None:
    """Add or remove exactly one relationship type, leaving the others untouched."""
    for spec in CARD_REFERENCE_SPECS:
        if not spec.mentioned_in(values):
            continue
        for entity_id in sorted(await _named_ids(session, values, spec)):
            exists = await session.get(spec.link_model, spec.link_key(card.id, entity_id))
            if linked != (exists is not None):
                await spec.toggle(session, card.id, entity_id, actor=ActorType.AI)
        return
    raise DomainError("A Card link proposal needs one relationship type")


class CardProposalHandler:
    entity = "card"
    version_model: type[Any] | None = Card

    async def prepare(
        self, context: PreparationContext, change: Any
    ) -> PreparedChange:
        card, expected_version = await require_target(context, change, Card)
        if card is not None:
            await reject_closed_repeat(context.session, card, change.entity)
        values = dict(change.values)
        proposed_kind = (
            values.get("kind") if change.action == "create" else getattr(card, "kind", None)
        )
        if proposed_kind != CardKind.ACTION.value:
            for action_only_field in ACTION_ONLY_FIELDS:
                values.pop(action_only_field, None)
            if proposed_kind == CardKind.GOAL.value and (
                values.get("parent_id") is not None or values.get("parent_query") is not None
            ):
                raise ToolPreparationError(
                    "invalid_parent_kind",
                    "A Goal is always root-level and cannot take a parent.",
                    "Drop the parent from this call, or propose an Idea or Action instead.",
                )
            if change.action == "update" and not values:
                raise DomainError("The Card proposal contains no applicable fields")
        await _resolve_parent_reference(context, values, str(proposed_kind))
        for spec in CARD_REFERENCE_SPECS:
            await _validate_named_references(context.session, values, spec)
        await _guard_pending_checks(context.session, change, values)
        return PreparedChange(values=values, expected_version=expected_version)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        values = dict(change.values)
        if change.action == "create":
            card = await create_card(
                session,
                kind=values["kind"],
                title=values["title"],
                note=values.get("note", ""),
                stage=values.get("stage", CardStage.BACKLOG.value),
                priority=values.get("priority", "medium"),
                hard_time=bool(values.get("hard_time", False)),
                blocked=bool(values.get("blocked", False)),
                blocked_description=values.get("blocked_description", ""),
                effort_points=values.get("effort_points"),
                repeatable=bool(values.get("repeatable", False)),
                parent_id=(
                    int(values["parent_id"]) if values.get("parent_id") is not None else None
                ),
                categories=set(values.get("categories") or []),
                energy_types=set(values.get("energy_types") or []),
                value_ids=await _named_ids(session, values, VALUE_REFERENCE),
                tag_ids=await _named_ids(session, values, TAG_REFERENCE),
                check_ids=await _named_ids(session, values, CHECK_REFERENCE),
                actor=ActorType.AI,
            )
            return [card.id]
        card = await session.get(Card, change.entity_id) if change.entity_id else None
        if card is None or card.version != change.expected_version:
            raise StaleStateError("A Card changed; refresh this proposal")
        if change.action == "move":
            await _apply_stage_change(session, card, CardStage(change.values["stage"]))
        elif change.action == "complete":
            await finish_action(session, card.id, CardStage.DONE, actor=ActorType.AI)
        elif change.action == "cancel":
            await finish_action(session, card.id, CardStage.CANCELLED, actor=ActorType.AI)
        elif change.action == "reopen":
            await _apply_stage_change(
                session, card, CardStage(change.values.get("stage", CardStage.BACKLOG.value))
            )
        elif change.action == "update":
            scalar_fields = {
                name: value
                for name, value in change.values.items()
                if name in CARD_SCALAR_FIELDS
            }
            if scalar_fields:
                await update_card_fields(session, card.id, scalar_fields, actor=ActorType.AI)
            if "parent_id" in change.values:
                await set_card_parent(
                    session, card.id, change.values["parent_id"], actor=ActorType.AI
                )
            if "stage" in change.values:
                await _apply_stage_change(session, card, CardStage(change.values["stage"]))
            await _replace_card_sets(session, card, change.values)
        elif change.action == "archive":
            await archive_subtree(session, card.id)
        elif change.action == "delete":
            if not context.allow_destructive:
                raise DomainError("Permanent deletion needs a second confirmation")
            await delete_subtree(session, card.id)
        elif change.action in {"link", "unlink"}:
            await _apply_card_links(
                session, card, change.values, linked=change.action == "link"
            )
        else:
            raise DomainError(f"Unsupported approved Card action: {change.action}")
        return [card.id]


class CheckProposalHandler:
    entity = "check"
    # A Check proposal keeps the version it was prepared against.
    version_model: type[Any] | None = None

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        check, expected_version = await require_target(context, change, Check)
        if check is not None:
            await reject_closed_repeat(context.session, check, change.entity)
        values = dict(change.values)
        await _validate_named_references(context.session, values, CHECK_VALUE_REFERENCE)
        return PreparedChange(values=values, expected_version=expected_version)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        values = dict(change.values)
        if change.action == "create":
            created = await create_check(
                session,
                title=str(values["title"]),
                repeatable=bool(values.get("repeatable", False)),
            )
            return [created.id]
        check = await session.get(Check, change.entity_id) if change.entity_id else None
        if check is None or check.version != change.expected_version:
            raise StaleStateError("A Check changed; refresh this proposal")
        if change.action == "update":
            scalar_fields = {
                name: value for name, value in values.items() if name in {"title", "repeatable"}
            }
            if scalar_fields:
                await update_check_fields(session, check.id, scalar_fields)
        elif change.action in CHECK_ANSWER_ACTIONS:
            await resolve_check(
                session, check.id, CHECK_ANSWER_ACTIONS[change.action], actor=ActorType.AI
            )
        elif change.action == "archive":
            await archive_check(session, check.id)
        elif change.action in {"link", "unlink"}:
            spec = CHECK_VALUE_REFERENCE
            for value_id in sorted(await _named_ids(session, values, spec)):
                exists = await session.get(spec.link_model, spec.link_key(check.id, value_id))
                if (change.action == "link") != (exists is not None):
                    await spec.toggle(session, check.id, value_id, actor=ActorType.AI)
        else:
            raise DomainError(f"Unsupported Check action: {change.action}")
        return [check.id]


class ValueProposalHandler:
    entity = "value"
    version_model: type[Any] | None = Value

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        _value, expected_version = await require_target(context, change, Value)
        return PreparedChange(values=dict(change.values), expected_version=expected_version)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        value = await session.get(Value, change.entity_id) if change.entity_id else None
        if change.action == "create":
            name = str(change.values["name"]).strip()
            if not name:
                raise DomainError("A new Value needs a name")
            value = await create_value(
                session,
                name,
                change.values.get("description"),
                active=change.values.get("active"),
            )
            await session.flush()
        else:
            if value is None or value.version != change.expected_version:
                raise StaleStateError("A Value changed; refresh this proposal")
            if change.action == "update":
                value = await update_value_fields(
                    session,
                    value.id,
                    name=change.values.get("name"),
                    description=change.values.get("description"),
                    active=change.values.get("active"),
                )
            elif change.action == "archive":
                value, _unlinked_count = await archive_value(session, value.id)
            else:
                raise DomainError(f"Unsupported Value action: {change.action}")
        return [value.id]


class TagProposalHandler:
    entity = "tag"
    version_model: type[Any] | None = Tag

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        _tag, expected_version = await require_target(context, change, Tag)
        return PreparedChange(values=dict(change.values), expected_version=expected_version)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        tag = await session.get(Tag, change.entity_id) if change.entity_id else None
        if change.action == "create":
            name = str(change.values.get("name", change.values.get("title", ""))).strip()
            if not name:
                raise DomainError("A new Tag needs a name")
            tag = await create_tag(session, name, change.values.get("description"))
            await session.flush()
        else:
            if tag is None or tag.version != change.expected_version:
                raise StaleStateError("A Tag changed; refresh this proposal")
            if change.action == "update":
                tag = await update_tag_fields(
                    session,
                    tag.id,
                    name=change.values.get("name"),
                    description=change.values.get("description"),
                )
            elif change.action == "archive":
                tag, _unlinked_count = await archive_tag(session, tag.id)
            else:
                raise DomainError(f"Unsupported Tag action: {change.action}")
        return [tag.id]
