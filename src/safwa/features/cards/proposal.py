"""How a proposed Card is checked and then written."""

from __future__ import annotations

import sqlite3
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.sql import RequestQueryError, UnsafeQueryError, normalize_request_sql
from ...domain import (
    CARD_REFERENCE_SPECS,
    CHECK_REFERENCE,
    TAG_REFERENCE,
    VALUE_REFERENCE,
    DomainError,
    StaleStateError,
    set_card_parent,
    toggle_card_category,
    toggle_card_energy_type,
)
from ...enums import (
    ActorType,
    CardKind,
    Category,
    EnergyType,
)
from ...models import Card, CardCategory, CardEnergyType, ProposalChange
from ..checks.api import unobserved_series
from ..proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    ToolPreparationError,
    named_ids,
    reject_closed_repeat,
    require_target,
    validate_named_references,
)
from .model import TERMINAL_STAGES, CardStage
from .use_cases import (
    archive_subtree,
    create_card,
    delete_subtree,
    finish_action,
    move_card,
    update_card_fields,
)

PARENT_HINT = (
    "Find the parent with query_safwa and retry with its numeric parent_id, or drop the "
    "parent. If you proposed it earlier in this same turn, wait for that result first."
)


STAGE_ACTIONS = frozenset({"move", "complete", "cancel", "reopen"})


ACTION_ONLY_FIELDS = (
    "effort_points",
    "repeatable",
    "categories",
    "energy_types",
    "blocked",
    "blocked_description",
)


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


def allows_parent(child_kind: str | None, parent_kind: str | None) -> bool:
    if child_kind == CardKind.IDEA.value:
        return parent_kind == CardKind.GOAL.value
    if child_kind == CardKind.ACTION.value:
        return parent_kind in {CardKind.GOAL.value, CardKind.IDEA.value}
    return False


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
    """Refuse to prepare a completion while a Check series on the Card has no answer.

    The error is model-visible and retryable, and it carries the titles so the model
    does not have to spend a `query_safwa` round discovering them.
    """
    completing = change.action == "complete" or (
        change.action in {"move", "update"} and values.get("stage") == CardStage.DONE.value
    )
    if not completing or change.id is None:
        return
    pending = await unobserved_series(session, int(change.id))
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
        target = await named_ids(session, values, spec)
        for entity_id in sorted(current ^ target):
            await spec.toggle(session, card.id, entity_id, actor=ActorType.AI)


async def _apply_card_links(
    session: AsyncSession, card: Card, values: dict[str, Any], *, linked: bool
) -> None:
    """Add or remove exactly one relationship type, leaving the others untouched."""
    for spec in CARD_REFERENCE_SPECS:
        if not spec.mentioned_in(values):
            continue
        for entity_id in sorted(await named_ids(session, values, spec)):
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
            if change.action in STAGE_ACTIONS or "stage" in values:
                raise ToolPreparationError(
                    "stage_is_action_only",
                    "A Goal and an Idea have no stage of their own: it shows what the Actions "
                    "under it are in.",
                    "Move, complete or reopen the Actions in its branch instead.",
                )
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
            await validate_named_references(context.session, values, spec)
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
                value_ids=await named_ids(session, values, VALUE_REFERENCE),
                tag_ids=await named_ids(session, values, TAG_REFERENCE),
                check_ids=await named_ids(session, values, CHECK_REFERENCE),
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
