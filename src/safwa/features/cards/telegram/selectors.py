"""Which relationships a Card screen offers, and the two screens that offer them.

A committed Card and a draft choose the same fields; the draft additionally chooses its
kind, which is immutable once the Card exists. Both selectors are drawn from one
vocabulary so a new relationship shows up on both without a second table to update.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....constants import SELECTOR_PAGE_SIZE
from ....enums import CardKind, Category, EnergyType, Priority
from ....foundation.errors import DomainError
from ....models import Card, CardCategory, CardEnergyType, CardTag, CardValue, Tag, Value
from ....shell import Page, Services, choice_rows, choice_screen, paginate
from ..model import CardStage
from ..use_cases import (
    EFFORT_POINTS,
    toggle_card_category,
    toggle_card_energy_type,
    toggle_card_tag,
    toggle_card_value,
)
from .creation import require_card_draft, sanitize_card_creation_state
from .presentation import CATEGORY_EMOJIS, ENERGY_EMOJIS, kind_label, typed_label


@dataclass(frozen=True)
class RelationChoice:
    """One overlapping Card relationship, described once for both selector surfaces."""

    singular: str
    draft_field: str
    link_column: Any
    link_owner: Any
    payload_key: str
    toggle: Callable[..., Awaitable[Any]]
    parse: Callable[[Any], Any]


RELATION_CHOICES: dict[str, RelationChoice] = {
    "categories": RelationChoice(
        "category",
        "categories",
        CardCategory.category,
        CardCategory.card_id,
        "value",
        toggle_card_category,
        Category,
    ),
    "energy": RelationChoice(
        "energy",
        "energy_types",
        CardEnergyType.energy_type,
        CardEnergyType.card_id,
        "value",
        toggle_card_energy_type,
        EnergyType,
    ),
    "values": RelationChoice(
        "value",
        "value_ids",
        CardValue.value_id,
        CardValue.card_id,
        "value_id",
        toggle_card_value,
        int,
    ),
    "tags": RelationChoice(
        "tag",
        "tag_ids",
        CardTag.tag_id,
        CardTag.card_id,
        "tag_id",
        toggle_card_tag,
        int,
    ),
}
# Single-valued selectors map a choice field to the Card column it sets.
SINGLE_CHOICE_FIELDS = {
    "kind": "kind",
    "stage": "stage",
    "priority": "priority",
    "effort": "effort_points",
}
CHOICE_TITLES = {
    "kind": "Choose Kind",
    "stage": "Choose Stage",
    "priority": "Choose Priority",
    "effort": "Choose Effort",
    "categories": "Categories",
    "energy": "Energy",
    "values": "Direct Values",
    "tags": "Tags",
}
CARD_CHOICE_FIELDS = ("stage", "priority", "effort", *RELATION_CHOICES)
CARD_DRAFT_CHOICE_FIELDS = ("kind", *CARD_CHOICE_FIELDS)
CARD_DRAFT_RELATIONS = {
    f"card_create_toggle_{relation.singular}": (relation.draft_field, relation.payload_key)
    for relation in RELATION_CHOICES.values()
}
CARD_RELATION_TOGGLES = {
    f"card_toggle_{relation.singular}": field
    for field, relation in RELATION_CHOICES.items()
}
NAMED_CHOICE_FIELDS = frozenset({"values", "tags"})


async def _choice_options(session: AsyncSession, field: str) -> list[tuple[str, Any]]:
    """The selectable options for one Card field, shared by draft and committed screens."""
    if field == "kind":
        return [(kind_label(kind), kind.value) for kind in CardKind]
    if field == "stage":
        return [
            (stage.value.title(), stage.value)
            for stage in (CardStage.BACKLOG, CardStage.SPRINT, CardStage.TODAY)
        ]
    if field == "priority":
        return [(priority.value.title(), priority.value) for priority in Priority]
    if field == "effort":
        return [(f"{points} EP", points) for points in sorted(EFFORT_POINTS)]
    if field == "categories":
        return [(typed_label(item, CATEGORY_EMOJIS), item.value) for item in Category]
    if field == "energy":
        return [(typed_label(item, ENERGY_EMOJIS), item.value) for item in EnergyType]
    if field not in {"values", "tags"}:
        raise DomainError("Unknown Card selector")
    model = Value if field == "values" else Tag
    items = await session.scalars(
        select(model).order_by(model.name)
    )
    return [(item.name, item.id) for item in items]


def _selector_page(field: str, options: list[tuple[str, Any]], page: int) -> Page | None:
    """Page the Value and Tag lists; the fixed enumerations always fit one screen."""
    if field not in NAMED_CHOICE_FIELDS:
        return None
    return paginate(options, page, SELECTOR_PAGE_SIZE)


async def handle_card_creation_chooser(
    message: Message, services: Services, action: str, *, page: int = 0
) -> None:
    """Render one selector for the transient Card draft; nothing is committed here."""
    field = action.removeprefix("card_create_choose_")
    relation = RELATION_CHOICES.get(field)
    async with services.sessions() as session:
        draft = await require_card_draft(session, services.owner_id)
        state = sanitize_card_creation_state(dict(draft.state or {}))
        options = await _choice_options(session, field)
        current = _selector_page(field, options, page)
        if relation is not None:
            selected = set(state[relation.draft_field])

            def build(value: Any, relation: RelationChoice = relation) -> tuple[str, dict]:
                return (
                    f"card_create_toggle_{relation.singular}",
                    {relation.payload_key: value},
                )
        else:
            state_field = SINGLE_CHOICE_FIELDS[field]
            selected = {state[state_field]}

            def build(value: Any, state_field: str = state_field) -> tuple[str, dict]:
                return "card_create_set", {"field": state_field, "value": value}

        choices = choice_rows(current.items if current else options, selected, build)
        await session.commit()
    await choice_screen(
        message,
        services,
        CHOICE_TITLES[field],
        choices,
        back=("↩️ Back", "card_create_view", {}),
        paging=(current, action, {}) if current else None,
    )


async def render_card_choices(
    message: Message, services: Services, action: str, card_id: int, *, page: int = 0
) -> None:
    """Render field and relationship selectors for an already committed Card.

    Every selection routes its mutation through the domain layer, and the screens
    themselves stay out of the persona dialogue.
    """
    field = action.removeprefix("card_choose_")
    if field not in CARD_CHOICE_FIELDS:
        raise DomainError("Unknown Card relationship selector")
    relation = RELATION_CHOICES.get(field)
    async with services.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None or card.archived_at is not None:
            raise DomainError("Card does not exist or is archived")
        options = await _choice_options(session, field)
        current = _selector_page(field, options, page)
        if relation is not None:
            selected = set(
                await session.scalars(
                    select(relation.link_column).where(relation.link_owner == card.id)
                )
            )

            def build(value: Any, relation: RelationChoice = relation) -> tuple[str, dict]:
                # The page rides along, so ticking one on page 2 comes back to page 2.
                return (
                    f"card_toggle_{relation.singular}",
                    {"id": card.id, relation.payload_key: value, "page": page},
                )
        elif field == "stage":
            # A stage change is a domain move, not a plain field write.
            selected = {card.effective_stage}

            def build(value: Any) -> tuple[str, dict]:
                return "card_move", {"id": card.id, "stage": value}

        else:
            column = SINGLE_CHOICE_FIELDS[field]
            selected = {getattr(card, column)}

            def build(value: Any, column: str = column) -> tuple[str, dict]:
                return "card_set_field", {"id": card.id, "field": column, "value": value}

        choices = choice_rows(current.items if current else options, selected, build)
        await session.commit()
    await choice_screen(
        message,
        services,
        CHOICE_TITLES[field],
        choices,
        back=("↩️ Back", "card_view", {"id": card_id}),
        paging=(current, action, {"id": card_id}) if current else None,
    )
