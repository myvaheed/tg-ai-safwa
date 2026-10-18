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
from ..schedules.agent import read_proposed_remind, read_proposed_schedule
from ..schedules.use_cases import set_remind
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
    PARENT_REFERENCE,
    TAG_REFERENCE,
    VALUE_REFERENCE,
)
from .use_cases import (
    archive_subtree,
    create_card,
    delete_card,
    finish_card,
    move_card,
    reopen_card,
    toggle_card_category,
    toggle_card_energy_type,
    update_card_fields,
    validate_parents,
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


async def _guard_parents(
    context: PreparationContext, change: Any, values: dict[str, Any], card_id: int | None
) -> None:
    """A parent the Card may stand under: a live Goal outside its branch, within the depth
    limit. Taking one away is always possible."""
    if not PARENT_REFERENCE.mentioned_in(values) or change.action is ChangeAction.UNLINK:
        return
    try:
        await validate_parents(
            context.session, card_id, await named_ids(context.session, values, PARENT_REFERENCE)
        )
    except DomainError as error:
        raise ToolPreparationError(
            "invalid_parent",
            str(error),
            "Choose a Goal outside this Card's branch, within the depth limit.",
        ) from error


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
    # Deleting a Card takes its history and what it gave its Sprints; the Cards under it stay.
    destructive_actions = frozenset({ChangeAction.DELETE})
    destructive_warning = (
        "This permanently removes this Card and its historical contribution; "
        "the Cards under it stay."
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
            refusal = await closed_repeat_refusal(
                context.session,
                card,
                change.entity,
                starts_again=change.action is ChangeAction.UPDATE and bool(values.get("schedule")),
            )
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
        for spec in CARD_REFERENCE_SPECS:
            await validate_named_references(context.session, values, spec)
        await _guard_parents(context, change, values, card.id if card else None)
        await _guard_pending_checks(context.session, change)
        await read_proposed_schedule(
            context, values, "action" if proposed_kind == CardKind.ACTION.value else "deadline"
        )
        await read_proposed_remind(context, values, card)
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
                parent_ids=await named_ids(session, values, PARENT_REFERENCE),
                categories=set(values.get("categories") or []),
                energy_types=set(values.get("energy_types") or []),
                value_ids=await named_ids(session, values, VALUE_REFERENCE),
                tag_ids=await named_ids(session, values, TAG_REFERENCE),
                check_ids=await named_ids(session, values, CHECK_REFERENCE),
                actor=ActorType.AI,
            )
            if values.get("remind") is not None:
                await set_remind(session, card, values["remind"], actor=ActorType.AI)
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
                card = await update_card_fields(session, card.id, scalar_fields, actor=ActorType.AI)
            if "stage" in change.values:
                await move_card(
                    session, card.id, CardStage(change.values["stage"]), actor=ActorType.AI
                )
            await _replace_card_sets(session, card, change.values)
            if "remind" in change.values:
                await set_remind(session, card, change.values["remind"], actor=ActorType.AI)
        elif change.action is ChangeAction.ARCHIVE:
            await archive_subtree(session, card.id, actor=ActorType.AI)
        elif change.action is ChangeAction.DELETE:
            if not context.allow_destructive:
                raise DomainError("Permanent deletion needs a second confirmation")
            await delete_card(session, card.id, actor=ActorType.AI)
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
