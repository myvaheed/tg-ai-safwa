"""The Card selector vocabulary: which relationships a Card screen offers, and how.

Everything else this module carried is in `safwa/shell/`. What is here moves into
`features/cards/` with the Card screens.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..enums import Category, EnergyType
from ..features.cards.references import TAG_REFERENCE, VALUE_REFERENCE
from ..features.cards.use_cases import (
    toggle_card_category,
    toggle_card_energy_type,
    toggle_card_tag,
    toggle_card_value,
)
from ..features.checks.api import CHECK_VALUE_REFERENCE
from ..models import CardCategory, CardEnergyType, CardTag, CardValue


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
# The committed-Card and draft selectors cover the same fields; a draft additionally
# chooses its kind, which is immutable once the Card exists.
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
# Tag and Value share one field-oriented item screen; the specs supply the differences.
# Every spec of one item names the same model, and together they are what carries it: a
# Value is on Checks as well as Cards, and the delete question has to count both.
ITEM_CARRIERS = {
    "tag": (TAG_REFERENCE,),
    "value": (VALUE_REFERENCE, CHECK_VALUE_REFERENCE),
}
