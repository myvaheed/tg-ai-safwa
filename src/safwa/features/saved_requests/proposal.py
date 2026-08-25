"""How a proposed Request is checked and then written.

Preparation normalises the SQL, so the row that reaches the review screen already holds
the statement Save will store.
"""

from __future__ import annotations

from typing import Any

from ...ai.sql import RequestQueryError, normalize_request_sql
from ...foundation.errors import DomainError, StaleStateError
from ...models import ProposalChange
from ..proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    ToolPreparationError,
    require_target,
)
from .model import SavedRequest
from .use_cases import (
    create_saved_request,
    delete_saved_request,
    update_saved_request,
)


class RequestProposalHandler:
    entity = "request"
    version_model: type[Any] | None = SavedRequest

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        _request, expected_version = await require_target(context, change, SavedRequest)
        values = dict(change.values)
        if "sql" in values:
            try:
                values["query_sql"] = normalize_request_sql(values.pop("sql"), context.views)
            except RequestQueryError as error:
                raise ToolPreparationError(
                    "unsafe_query",
                    f"Invalid Request SQL: {error}",
                    "Use one read-only SELECT over ai_cards that returns an id column.",
                ) from error
        return PreparedChange(values=values, expected_version=expected_version)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        request = (
            await session.get(SavedRequest, change.entity_id) if change.entity_id else None
        )
        if change.action == "create":
            request = await create_saved_request(
                session,
                str(change.values["name"]),
                change.values["query_sql"],
                change.values.get("description"),
                views=context.views,
            )
        elif request is None or request.version != change.expected_version:
            raise StaleStateError("A Request changed; refresh this proposal")
        elif change.action == "update":
            request = await update_saved_request(
                session,
                request.id,
                name=(str(change.values["name"]) if "name" in change.values else None),
                description=(
                    str(change.values["description"])
                    if "description" in change.values
                    else None
                ),
                query_sql=change.values.get("query_sql"),
                views=context.views,
            )
        elif change.action == "delete":
            request = await delete_saved_request(session, request.id)
        else:
            raise DomainError(f"Unsupported Request action: {change.action}")
        return [request.id]
