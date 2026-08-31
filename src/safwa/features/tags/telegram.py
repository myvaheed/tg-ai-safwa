"""How a Tag reads to the owner: its review screen, and the screen a citation opens."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...telegram._presentation import short_citation_title
from ...telegram.items import render_item_editor
from ..proposals.api import (
    NamedItemPresenter,
)
from .model import Tag


class TagProposalPresenter(NamedItemPresenter):
    entity = "tag"
    model = Tag
    label = "Tag"

    def _current(self, item: Any) -> dict[str, Any]:
        return {"name": item.name, "description": item.description}


async def tag_citation_label(session: AsyncSession, services: Any, tag: Tag) -> str:
    return f"🏷 {short_citation_title(tag.name)}"


async def open_tag(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    await render_item_editor(
        message, services, "tag", mode="view", item_id=item_id, replace=replace
    )
