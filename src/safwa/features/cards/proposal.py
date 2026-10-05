"""How a proposed Card is checked and then written."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError, StaleStateError
from tg_agent_shell.proposals.api import (
    ApplyContext,
    ChangeAction,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
    named_ids,
    require_target,
    set_named_links,
    validate_named_references,
)

from ...enums import ActorType
from ...foundation.marks import closed_repeat_refusal
from ..checks.use_cases import pending_checks
from ..profile.api import effort_tracking_on
from ..schedules.agent import read_proposed_schedule
from .model import (
    Card,
    CardCategory,
    CardEnergyType,
    CardKind,
    CardStage,
    Category,
    EnergyType,
)
from .references import (
    CARD_REFERENCE_SPECS,
    CHECK_REFERENCE,
    TAG_REFERENCE,
    VALUE_REFERENCE,
)
from .use_cases import (
    archive_subtree,
    create_card,
    delete_subtree,
    finish_card,
    holds_subgoals,
    move_card,
    reopen_card,
    require_finished_actions,
    set_card_parent,
    toggle_card_category,
    toggle_card_energy_type,
    update_card_fields,
)

PARENT_HINT = (
    "Find the parent with query_data and retry with its id as parent, or drop the "
    "parent. If you proposed it earlier in this same turn, wait for that result first."
)


CARD_SCALAR_FIELDS = frozenset(
    {
        "title",
        "note",
        "priority",
        "schedule",
        "schedule_rule",
        "blocked_description",
        "effort_points",
        "tracked_mins",
    }
)


def allows_parent(child_kind: str | None, parent_kind: str | None) -> bool:
    # A Goal given a parent is the Subgoal it becomes.
    if child_kind in {CardKind.GOAL.value, CardKind.SUBGOAL.value}:
        return parent_kind == CardKind.GOAL.value
    if child_kind == CardKind.ACTION.value:
        return parent_kind in {CardKind.GOAL.value, CardKind.SUBGOAL.value}
    return False


async def _resolve_parent_reference(
    context: PreparationContext, values: dict[str, Any], child_kind: str | None
) -> None:
    """Turn `parent`, an id or an exact title, into the `parent_id` Save writes."""
    session = context.session
    if "parent" in values:
        parent = values.pop("parent")
        if parent is None or isinstance(parent, int):
            values["parent_id"] = parent
        else:
            title = str(parent).strip()
            if not title:
                raise ToolPreparationError(
                    "invalid_arguments",
                    "Parent title must not be empty.",
                    "Provide one exact Card title or its id.",
                )
            matches = list(
                await session.scalars(
                    select(Card).where(
                        Card.title.collate("NOCASE") == title,
                        Card.archived_at.is_(None),
                    )
                )
            )
            if not matches:
                raise ToolPreparationError(
                    "reference_not_found",
                    f"Parent Card '{title}' was not found.",
                    PARENT_HINT,
                )
            if len(matches) > 1:
                raise ToolPreparationError(
                    "reference_ambiguous",
                    f"Parent Card '{title}' matched more than one Card.",
                    "Use query_data to choose one parent and retry with its id.",
                )
            values["parent_id"] = matches[0].id

    if "parent_id" not in values:
        return
    parent_id = values["parent_id"]
    if parent_id is None:
        if child_kind == CardKind.SUBGOAL.value:
            raise ToolPreparationError(
                "parent_required",
                "A Subgoal is always under a Goal.",
                "Keep its parent, or name another Goal as parent.",
            )
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


def _refuse_wrong_tool(card: Card, addressed: str | None) -> None:
    """A call names the kind its tool writes; one on a Card of another kind goes back."""
    if addressed is None:
        return
    is_action = card.kind == CardKind.ACTION.value
    if is_action == (addressed == CardKind.ACTION.value):
        return
    article = "an" if is_action else "a"
    raise ToolPreparationError(
        "wrong_tool",
        f"Card #{card.id} is {article} {card.kind.title()}.",
        f"Call {'action' if is_action else 'goal'} with this id.",
    )


async def _guard_pending_checks(session: AsyncSession, change: Any) -> None:
    """Refuse to prepare a completion while a Check series on the Card has no answer.

    The error is model-visible and retryable, and it carries the titles so the model
    does not have to spend a `query_data` round discovering them.
    """
    if change.action is not ChangeAction.COMPLETE or change.id is None:
        return
    pending = await pending_checks(session, int(change.id))
    if not pending:
        return
    listed_checks = ", ".join(f"#{check.id} “{check.title}”" for check in pending)
    raise ToolPreparationError(
        "pending_checks",
        f"Card #{change.id} still has Pending Checks: {listed_checks}.",
        "Answer each one first: check(mode='passed'|'missed', id=…) when the user already "
        "said how it went, otherwise cite them as [title](check:<id>) so they answer them "
        "themselves. Then retry only this unfinished completion.",
    )


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
        await set_named_links(session, spec, card.id, values, linked=linked, actor=ActorType.AI)
        return
    raise DomainError("A Card link proposal needs one relationship type")


class CardProposalHandler:
    entity = "card"
    version_model: type[Any] | None = Card
    # Deleting a Card takes its whole subtree and that subtree's historical contribution.
    destructive_actions = frozenset({ChangeAction.DELETE})
    destructive_warning = (
        "This permanently removes the selected tree and its historical contribution."
    )

    async def prepare(
        self, context: PreparationContext, change: Any
    ) -> PreparedChange:
        card, expected_version = await require_target(context, change, Card)
        values = dict(change.values)
        if card is not None:
            _refuse_wrong_tool(card, values.pop("kind", None))
        # Time and effort supplied after completion belong to the finished instance;
        # its successor was already copied, so other edits still target the open repeat.
        accounting_only = (
            change.action is ChangeAction.UPDATE
            and bool(values)
            and set(values) <= {"tracked_mins", "effort_points"}
        )
        if card is not None and not accounting_only:
            refusal = await closed_repeat_refusal(context.session, card, change.entity)
            if refusal is not None:
                raise ToolPreparationError(*refusal)
        if "effort_points" in values and not await effort_tracking_on(context.session):
            values.pop("effort_points")
            if change.action is ChangeAction.UPDATE and not values:
                raise ToolPreparationError(
                    "effort_points_off",
                    "Effort Points are off, so no estimate is saved.",
                    "Tell the user to turn on Effort Points in the Profile first.",
                )
        proposed_kind = (
            values.get("kind") if change.action is ChangeAction.CREATE else getattr(card, "kind", None)
        )
        if (
            proposed_kind != CardKind.ACTION.value
            and change.action is ChangeAction.COMPLETE
            and card is not None
        ):
            await require_finished_actions(context.session, card.id)
        await _resolve_parent_reference(context, values, str(proposed_kind))
        if card is not None and card.kind == CardKind.GOAL.value and values.get("parent_id"):
            if await holds_subgoals(context.session, card.id):
                raise ToolPreparationError(
                    "invalid_parent_kind",
                    f"Goal “{card.title}” has Subgoals under it and cannot become a Subgoal.",
                    "Leave it root-level, or move its Subgoals elsewhere first.",
                )
            # What Save will do, so the review screen and the receipt say it.
            values["kind"] = CardKind.SUBGOAL.value
        for spec in CARD_REFERENCE_SPECS:
            await validate_named_references(context.session, values, spec)
        await _guard_pending_checks(context.session, change)
        await read_proposed_schedule(
            context, values, "action" if proposed_kind == CardKind.ACTION.value else "deadline"
        )
        return PreparedChange(values=values, expected_version=expected_version)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        values = dict(change.values)
        if change.action is ChangeAction.CREATE:
            card = await create_card(
                session,
                kind=values["kind"],
                title=values["title"],
                note=values.get("note", ""),
                stage=values.get("stage", CardStage.BACKLOG.value),
                priority=values.get("priority", "medium"),
                schedule=values.get("schedule"),
                schedule_rule=values.get("schedule_rule"),
                blocked_description=values.get("blocked_description") or "",
                effort_points=values.get("effort_points"),
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
        if change.action is ChangeAction.MOVE:
            await move_card(
                session, card.id, CardStage(change.values["stage"]), actor=ActorType.AI
            )
        elif change.action is ChangeAction.COMPLETE:
            await finish_card(
                session,
                card.id,
                actor=ActorType.AI,
                tracked_mins=change.values.get("tracked_mins"),
            )
        elif change.action is ChangeAction.REOPEN:
            await reopen_card(
                session, card.id, CardStage(change.values.get("stage", CardStage.BACKLOG.value)),
                actor=ActorType.AI,
            )
        elif change.action is ChangeAction.UPDATE:
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
                await move_card(
                    session, card.id, CardStage(change.values["stage"]), actor=ActorType.AI
                )
            await _replace_card_sets(session, card, change.values)
        elif change.action is ChangeAction.ARCHIVE:
            await archive_subtree(session, card.id, actor=ActorType.AI)
        elif change.action is ChangeAction.DELETE:
            if not context.allow_destructive:
                raise DomainError("Permanent deletion needs a second confirmation")
            await delete_subtree(session, card.id, actor=ActorType.AI)
        elif change.action in {ChangeAction.LINK, ChangeAction.UNLINK}:
            await _apply_card_links(
                session, card, change.values, linked=change.action is ChangeAction.LINK
            )
        else:
            raise DomainError(f"Unsupported approved Card action: {change.action}")
        return [card.id]


async def open_cards(session: AsyncSession) -> list[tuple[int, str]]:
    """Every Card not Done and not archived, whatever its kind (PR-SIMILAR-030)."""
    rows = await session.execute(
        select(Card.id, Card.title).where(
            Card.effective_stage != CardStage.DONE.value, Card.archived_at.is_(None)
        )
    )
    return [(card_id, title) for card_id, title in rows]
