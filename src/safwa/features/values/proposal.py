"""How a proposed Value is checked and then written."""

from __future__ import annotations

from typing import Any

from ...foundation.errors import DomainError, StaleStateError
from ...models import ProposalChange
from ..proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    require_target,
)
from .model import Value
from .use_cases import archive_value, create_value, update_value_fields


class ValueProposalHandler:
    entity = "value"
    version_model: type[Any] | None = Value

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        _value, expected_version = await require_target(context, change, Value)
        return PreparedChange(values=dict(change.values), expected_version=expected_version)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        value = await session.get(Value, change.entity_id) if change.entity_id else None
        if change.action == "create":
            name = str(change.values["name"]).strip()
            if not name:
                raise DomainError("A new Value needs a name")
            value = await create_value(
                session,
                name,
                change.values.get("description"),
                active=change.values.get("active"),
            )
            await session.flush()
        else:
            if value is None or value.version != change.expected_version:
                raise StaleStateError("A Value changed; refresh this proposal")
            if change.action == "update":
                value = await update_value_fields(
                    session,
                    value.id,
                    name=change.values.get("name"),
                    description=change.values.get("description"),
                    active=change.values.get("active"),
                )
            elif change.action == "archive":
                value, _unlinked_count = await archive_value(session, value.id)
            else:
                raise DomainError(f"Unsupported Value action: {change.action}")
        return [value.id]
