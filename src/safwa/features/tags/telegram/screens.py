"""The Tags the owner keeps: the list, one Tag, and what a tap on it does."""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....enums import MessageKind
from ....foundation.errors import DomainError
from ....foundation.screens import TextInputFlow
from ....models import UiSession
from ....shell import (
    CallbackContext,
    CallbackHandler,
    Services,
    TextInputScreen,
    TextValidator,
    edit_registered_message,
    menu_row,
    render_text_input,
    required_text,
    send_registered,
    token_button,
)
from ..model import Tag
from ..use_cases import create_tag, delete_tag, tag_link_count, update_tag_fields

_EDITOR_TTL = timedelta(minutes=30)


async def command_tags(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        tags = list(await session.scalars(select(Tag).order_by(Tag.name)))
        rows = [
            [
                await token_button(
                    session, services.owner_id, tag.name, "tag_view", {"id": tag.id}
                )
            ]
            for tag in tags
        ]
        rows.append(
            [await token_button(session, services.owner_id, "➕ Add Tag", "tag_create_prompt", {})]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Tags</b>\nUse Tags to group Cards independently of Values.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


async def render_tag(
    message: Message,
    services: Services,
    *,
    mode: str,
    item_id: int | None = None,
    values: dict[str, str] | None = None,
    replace_message_id: int | None = None,
    replace: bool | None = None,
) -> None:
    if mode not in {"create", "view"}:
        raise DomainError("Unsupported Tag editor")
    carried_by = 0
    async with services.sessions() as session:
        tag: Tag | None = None
        if mode == "view":
            tag = await session.get(Tag, item_id)
            if tag is None:
                raise DomainError("Tag does not exist")
            editor_values = {"name": tag.name, "description": tag.description}
            carried_by = await tag_link_count(session, tag.id)
        else:
            editor_values = {"name": "", "description": "", **(values or {})}

        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="item_editor",
                state={
                    "entity": "tag",
                    "mode": mode,
                    "item_id": item_id,
                    "values": editor_values,
                    "message_id": replace_message_id or message.message_id,
                },
                expires_at=datetime.now(UTC) + _EDITOR_TTL,
            )
        )
        rows: list[list[InlineKeyboardButton]] = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    "✏️ Name",
                    "tag_edit_text",
                    {"mode": mode, "id": item_id, "field": "name"},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "📝 Description",
                    "tag_edit_text",
                    {"mode": mode, "id": item_id, "field": "description"},
                ),
            ]
        ]
        if tag is not None:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        "Delete Tag",
                        "tag_delete_prompt",
                        {"id": tag.id},
                    )
                ]
            )
        elif editor_values["name"].strip():
            rows.append(
                [await token_button(session, services.owner_id, "✅ Create Tag", "tag_create", {})]
            )
        rows.append([await token_button(session, services.owner_id, "↩️ Back", "tag_back", {})])
        await session.commit()

    body = (
        f"<b>{'Create Tag' if mode == 'create' else 'Tag'}</b>\n"
        f"Name: {html.escape(editor_values['name'] or '—')}\n"
        f"Description: {html.escape(editor_values['description'] or '—')}"
        + (f"\nLinked Cards: {carried_by}" if mode == "view" else "")
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            body,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=item_id,
        )
    else:
        await send_registered(
            message,
            services,
            body,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=item_id,
            replace=replace,
        )


async def open_tag(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    await render_tag(message, services, mode="view", item_id=item_id, replace=replace)


async def render_tag_text_prompt(
    message: Message, services: Services, *, mode: str, item_id: int | None, field: str
) -> None:
    if field not in {"name", "description"}:
        raise DomainError("Unsupported text field")
    async with services.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == services.owner_id)
        )
        if editor is None or editor.kind != "item_editor":
            raise DomainError("Item editor expired")
        state = dict(editor.state)
        state["field"] = field
        state["flow"] = "tag"
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title=f"Edit Tag {field.title()}",
            current_value=str(state.get("values", {}).get(field, "")),
            instruction=f"Send the new {field}.",
            back_action="tag_text_back",
            back_payload={"mode": mode, "id": item_id},
            related_id=item_id,
        ),
        state=state,
    )


def _validator(state: Mapping[str, Any]) -> TextValidator[str] | None:
    return required_text("Tag name") if state["field"] == "name" else None


async def _apply_text(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: str
) -> None:
    """A Tag being created is not saved yet, so the typed value stays in the editor."""
    del services
    if state["mode"] == "view":
        await update_tag_fields(session, int(state["item_id"]), **{str(state["field"]): value})


async def _render_after_text(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    item_id = state.get("item_id")
    await render_tag(
        message,
        services,
        mode=str(state["mode"]),
        item_id=int(item_id) if item_id is not None else None,
        values={**dict(state.get("values", {})), str(state["field"]): value},
        replace_message_id=int(state["text_input"]["message_id"]),
    )


TEXT_INPUT = TextInputFlow(
    name="tag",
    validator=_validator,
    apply=_apply_text,
    render=_render_after_text,
)


async def _on_create_prompt(context: CallbackContext) -> None:
    await render_tag(context.message, context.services, mode="create")


async def _on_view(context: CallbackContext) -> None:
    await render_tag(context.message, context.services, mode="view", item_id=context.payload["id"])


async def _on_edit_text(context: CallbackContext) -> None:
    await render_tag_text_prompt(
        context.message,
        context.services,
        mode=context.payload["mode"],
        item_id=context.payload.get("id"),
        field=context.payload["field"],
    )


async def _on_text_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        values = dict(editor.state.get("values", {})) if editor is not None else {}
    await render_tag(
        context.message,
        context.services,
        mode=context.payload["mode"],
        item_id=context.payload.get("id"),
        values=values,
    )


async def _on_create(context: CallbackContext) -> None:
    async with context.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        if editor is None or editor.kind != "item_editor":
            raise DomainError("Item editor expired")
        values = dict(editor.state.get("values", {}))
        # A description box the owner never typed into is not a description they gave.
        tag = await create_tag(
            session, values.get("name", ""), values.get("description", "").strip() or None
        )
        await session.commit()
    await render_tag(context.message, context.services, mode="view", item_id=tag.id)


async def _on_delete_prompt(context: CallbackContext) -> None:
    async with context.sessions() as session:
        tag = await session.get(Tag, context.payload["id"])
        if tag is None:
            raise DomainError("Tag does not exist")
        cards = await tag_link_count(session, tag.id)
        confirm = await token_button(
            session, context.owner_id, "Delete Tag", "tag_delete_confirm", {"id": tag.id}
        )
        back = await token_button(
            session, context.owner_id, "↩️ Back", "tag_view", {"id": tag.id}
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Delete Tag?</b>\n{html.escape(tag.name)} will be deleted and taken off "
        f"{_carrier_phrase(cards)}. Nothing it is on is deleted.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [back]]),
        related_id=tag.id,
    )


def _carrier_phrase(cards: int) -> str:
    """One wording for the delete question and for the receipt that answers it."""
    return f"{cards} Card{'' if cards == 1 else 's'}" if cards else "nothing"


async def _on_delete_confirm(context: CallbackContext) -> None:
    async with context.sessions() as session:
        tag, removed = await delete_tag(session, context.payload["id"])
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        back = await token_button(session, context.owner_id, "Back to Tags", "tag_back", {})
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Deleted <b>{html.escape(tag.name)}</b>. Taken off {removed} link(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
        related_id=tag.id,
    )


async def _on_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
    await command_tags(context.message, context.services)


TAG_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "tag_create_prompt": _on_create_prompt,
    "tag_view": _on_view,
    "tag_edit_text": _on_edit_text,
    "tag_text_back": _on_text_back,
    "tag_create": _on_create,
    "tag_delete_prompt": _on_delete_prompt,
    "tag_delete_confirm": _on_delete_confirm,
    "tag_back": _on_back,
}
