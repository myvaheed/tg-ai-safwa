"""The named relationships carried by a Card: its parents, Values, Tags and Checks.

Declared here rather than in `api.py` because each spec names the toggle that writes it, and
`api.py` cannot reach the Card use cases: Planning reads through that door while the use
cases read through Planning's.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.references import ReferenceSpec

from ...foundation.marks import live_instance_hint
from ..checks.model import Check
from ..tags.api import CardTag, Tag
from ..values.api import CardValue, Value
from .model import Card, CardCheck, CardParent
from .use_cases import (
    toggle_card_check,
    toggle_card_parent,
    toggle_card_tag,
    toggle_card_value,
)


async def _closed_check_refusal(
    session: AsyncSession, check: Check
) -> tuple[str, str, str] | None:
    """A closed instance links to a row the owner can no longer act on."""
    if not check.is_closed_repeat():
        return None
    return (
        "closed_repeat",
        f"Check #{check.id} is a closed repeat and cannot be linked.",
        await live_instance_hint(session, check),
    )


VALUE_REFERENCE = ReferenceSpec(
    "value", "Value", Value, CardValue, toggle_card_value, owner="Card",
    order_attr="order",
)
TAG_REFERENCE = ReferenceSpec("tag", "Tag", Tag, CardTag, toggle_card_tag, owner="Card")
# A Check is a Card relationship like the other two, so it resolves, diffs and applies
# through the same spec; only the name column differs.
CHECK_REFERENCE = ReferenceSpec(
    "check",
    "Check",
    Check,
    CardCheck,
    toggle_card_check,
    owner="Card",
    name_attr="title",
    archivable=True,
    refusal=_closed_check_refusal,
)
# A parent is a Card the Card stands under, named by its title; whether it may be one is
# the Card's own question, asked by `validate_parents` before review.
PARENT_REFERENCE = ReferenceSpec(
    "parent",
    "Parent",
    Card,
    CardParent,
    toggle_card_parent,
    owner="Card",
    name_attr="title",
    archivable=True,
)
CARD_REFERENCE_SPECS = (PARENT_REFERENCE, VALUE_REFERENCE, TAG_REFERENCE, CHECK_REFERENCE)
