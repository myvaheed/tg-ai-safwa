"""The named relationships carried by a Card.

Declared here rather than in `api.py` because each spec names the toggle that writes it, and
`api.py` cannot reach the Card use cases: Planning reads through that door while the use
cases read through Planning's.
"""

from __future__ import annotations

from ...foundation.references import ReferenceSpec
from ..checks.api import Check
from ..tags.api import CardTag, Tag
from ..values.api import CardValue, Value
from .model import CardCheck
from .use_cases import toggle_card_check, toggle_card_tag, toggle_card_value

VALUE_REFERENCE = ReferenceSpec("value", "Value", Value, CardValue, toggle_card_value)
TAG_REFERENCE = ReferenceSpec("tag", "Tag", Tag, CardTag, toggle_card_tag)
# A Check is a Card relationship like the other two, so it resolves, diffs and applies
# through the same spec; only the name column differs.
CHECK_REFERENCE = ReferenceSpec(
    "check",
    "Check",
    Check,
    CardCheck,
    toggle_card_check,
    name_attr="title",
    archivable=True,
)
CARD_REFERENCE_SPECS = (VALUE_REFERENCE, TAG_REFERENCE, CHECK_REFERENCE)
