"""How a proposed Tag reads to the owner, and how a saved one reads when it is cited."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.proposals.render import NamedItemPresenter
from tg_agent_shell.telegram import short_citation_title

from ..model import Tag


class TagProposalPresenter(NamedItemPresenter):
    entity = "tag"
    model = Tag
    label = "Tag"

    def _current(self, item: Any) -> dict[str, Any]:
        return {"name": item.name, "description": item.description}


async def tag_citation_label(session: AsyncSession, services: Any, tag: Tag) -> str:
    return f"🏷 {short_citation_title(tag.name)}"
