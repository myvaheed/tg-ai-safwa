"""What Edit offers on a Card proposal: the controls the Card's own screen has (CD-EDIT-067).

In the order that screen lists them, under its labels, with its choices and its words. A
Card's Goals and Checks are not offered, and neither is its kind.
"""

from __future__ import annotations

from collections.abc import Mapping

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.references import ReferenceSpec
from tg_agent_shell.proposals.api import EditableField

from ....constants import SELECTOR_PAGE_SIZE
from ..model import CardKind
from ..references import TAG_REFERENCE, VALUE_REFERENCE
from .selectors import CHOICE_TITLES, choice_options
from .text_input import CARD_MARKDOWN_FIELDS, card_text_prompt, card_text_validator


def _typed(label: str, field: str, *, deadline: bool = False) -> EditableField:
    title, instruction = card_text_prompt(field, deadline=deadline)
    return EditableField(
        label,
        title,
        parse=card_text_validator(field),
        instruction=instruction,
        markdown=field in CARD_MARKDOWN_FIELDS,
    )


def _chosen(
    label: str, choice: str, *, many: bool = False, reference: ReferenceSpec | None = None
) -> EditableField:
    return EditableField(
        label,
        CHOICE_TITLES[choice],
        options=lambda session: choice_options(session, choice),
        many=many,
        reference=reference,
        page_size=SELECTOR_PAGE_SIZE if reference is not None else None,
    )


_LINKS = {
    "values": _chosen("💎 Values", "values", many=True, reference=VALUE_REFERENCE),
    "tags": _chosen("🏷 Tags", "tags", many=True, reference=TAG_REFERENCE),
}
ACTION_FIELDS = {
    "title": _typed("✏️ Title", "title"),
    "note": _typed("📝 Note", "note"),
    "stage": _chosen("📍 Stage", "stage"),
    "priority": _chosen("⚠️ Priority", "priority"),
    "schedule": _typed("⏱ Schedule", "schedule"),
    "blocked_description": _typed("📝 Blocked reason", "blocked_description"),
    "effort_points": _chosen("🔢 Effort", "effort"),
    "tracked_mins": _typed("⌛ Time spent", "tracked_mins"),
    "categories": _chosen("🏷 Categories", "categories", many=True),
    "energy_types": _chosen("⚡ Energy", "energy", many=True),
    **_LINKS,
}
GOAL_FIELDS = {
    "title": _typed("✏️ Title", "title"),
    "note": _typed("📝 Note", "note"),
    "priority": _chosen("⚠️ Priority", "priority"),
    "schedule": _typed("⏰ Deadline", "schedule", deadline=True),
    **_LINKS,
}


def card_review_fields(call: AgentChange) -> Mapping[str, EditableField]:
    """Every Card call names the kind it was made for, and the two kinds' screens differ."""
    return ACTION_FIELDS if call.values.get("kind") == CardKind.ACTION.value else GOAL_FIELDS
