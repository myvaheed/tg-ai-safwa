"""How a proposed Tag is checked and then written."""

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
    require_target,
)

from .model import Tag
from .use_cases import create_tag, delete_tag, update_tag_fields


class TagProposalHandler:
    entity = "tag"
    version_model: type[Any] | None = Tag

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        _tag, expected_version = await require_target(context, change, Tag)
        return PreparedChange(values=dict(change.values), expected_version=expected_version)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        tag = await session.get(Tag, change.entity_id) if change.entity_id else None
        if change.action is ChangeAction.CREATE:
            name = str(change.values.get("name", change.values.get("title", ""))).strip()
            if not name:
                raise DomainError("A new Tag needs a name")
            tag = await create_tag(session, name, change.values.get("description"))
            await session.flush()
        else:
            if tag is None or tag.version != change.expected_version:
                raise StaleStateError("A Tag changed; refresh this proposal")
            if change.action is ChangeAction.UPDATE:
                tag = await update_tag_fields(
                    session,
                    tag.id,
                    name=change.values.get("name"),
                    description=change.values.get("description"),
                )
            elif change.action is ChangeAction.DELETE:
                tag, _unlinked_count = await delete_tag(session, tag.id)
            else:
                raise DomainError(f"Unsupported Tag action: {change.action}")
        return [tag.id]


async def every_tag(session: AsyncSession) -> list[tuple[int, str]]:
    """Every Tag: none is ever closed (PR-SIMILAR-030)."""
    rows = await session.execute(select(Tag.id, Tag.name))
    return [(tag_id, name) for tag_id, name in rows]
