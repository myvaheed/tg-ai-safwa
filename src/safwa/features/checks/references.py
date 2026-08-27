"""The named Value relationship carried by a Check.

Same shape as a Card's, on the other side of the junction row.
"""

from __future__ import annotations

from ...foundation.references import ReferenceSpec
from ..values.api import CheckValue, Value
from .use_cases import toggle_check_value

CHECK_VALUE_REFERENCE = ReferenceSpec(
    "value", "Value", Value, CheckValue, toggle_check_value, owner="Check"
)
