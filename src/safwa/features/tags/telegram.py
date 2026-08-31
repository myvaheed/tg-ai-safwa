"""How a Tag reads to the owner: its review screen, and the screen a citation opens."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.screens import TextInputFlow
from ...telegram._presentation import short_citation_title
from ...telegram.items import render_item_editor
from ...telegram.text_input import TextValidator, required_text
from ..proposals.api import (
    NamedItemPresenter,
)
from .model import Tag
from .use_cases import update_tag_fields


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


def _item_validator(state: Mapping[str, Any]) -> TextValidator[str] | None:
    return required_text(f"{"tag".title()} name") if state["field"] == "name" else None


async def _apply_item_text(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: str
) -> None:
    """An item being created is not saved yet, so the typed value stays in the editor."""
    del services
    if state["mode"] == "view":
        await update_tag_fields(session, int(state["item_id"]), **{str(state["field"]): value})


async def _render_item_editor(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    item_id = state.get("item_id")
    await render_item_editor(
        message,
        services,
        "tag",
        mode=str(state["mode"]),
        item_id=int(item_id) if item_id is not None else None,
        values={**dict(state.get("values", {})), str(state["field"]): value},
        replace_message_id=int(state["text_input"]["message_id"]),
    )


TEXT_INPUT = TextInputFlow(
    name="tag",
    validator=_item_validator,
    apply=_apply_item_text,
    render=_render_item_editor,
)
