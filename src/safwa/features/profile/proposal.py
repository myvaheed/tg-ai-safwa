"""How a proposal to change the Profile is checked and then saved.

Each field is checked the way the Profile screen checks it, before the owner sees a screen,
and Save stores each through `set_profile_field`: one saved field is one change
(PS-REVISION-011). The switches of the automatic reactions are not fields, and the tool
has none.
"""

from __future__ import annotations

from typing import Any

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
)

from ..reminders.api import parse_clock
from .model import ProfileField, ProfileValue
from .use_cases import profile_field, set_profile_field, validated_profile_value

_CLOCKS = frozenset({ProfileField.MORNING_TIME, ProfileField.EVENING_TIME})


def stored_value(field: ProfileField, value: Any) -> ProfileValue:
    """The tool's value as the field takes it: a time from HH:MM, the rest as sent."""
    if field in _CLOCKS and isinstance(value, str):
        try:
            return parse_clock(value)
        except ValueError:
            raise DomainError(f"{field.value} must be a time as HH:MM") from None
    return value


class ProfileProposalHandler:
    entity = "profile"
    version_model: type[Any] | None = None

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        for name, value in change.values.items():
            try:
                field = profile_field(name)
                validated_profile_value(field, stored_value(field, value))
            except DomainError as error:
                raise ToolPreparationError(
                    "invalid_value",
                    f"{name}: {error}.",
                    "Tell the user why, in one line, and ask for a value that fits. "
                    "Propose nothing.",
                ) from None
        return PreparedChange(values=dict(change.values), expected_version=None)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        for name, value in change.values.items():
            field = profile_field(name)
            await set_profile_field(context.session, field, stored_value(field, value))
        return []
