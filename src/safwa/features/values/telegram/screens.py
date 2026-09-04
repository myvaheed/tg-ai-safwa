"""The Values the owner keeps: the list, one Value, and what a tap on it does."""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.adapters.kinds import MessageKind
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.screens import TextInputFlow
from tg_agent_shell.telegram import (
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
from tg_agent_shell.telegram.model import UiSession

from ..model import Value
from ..use_cases import (
    create_value,
    delete_value,
    set_value_focus,
    update_value_fields,
    value_link_counts,
)

_EDITOR_TTL = timedelta(minutes=30)


async def command_values(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        values = list(await session.scalars(select(Value).order_by(Value.name)))
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"{'✅' if value.active else '○'} {value.name}",
                    "value_view",
                    {"id": value.id},
                )
            ]
            for value in values
        ]
        rows.append(
            [await token_button(session, services.owner_id, "➕ Add Value", "value_create_prompt", {})]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Values in focus</b>\nActive Values are injected into the advisor context.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


async def render_value(
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
        raise DomainError("Unsupported Value editor")
    carried_by: tuple[int, int] = (0, 0)
    async with services.sessions() as session:
        value: Value | None = None
        if mode == "view":
            value = await session.get(Value, item_id)
            if value is None:
                raise DomainError("Value does not exist")
            editor_values = {"name": value.name, "description": value.description}
            carried_by = await value_link_counts(session, value.id)
        else:
            editor_values = {"name": "", "description": "", **(values or {})}

        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="item_editor",
                state={
                    "entity": "value",
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
                    "value_edit_text",
                    {"mode": mode, "id": item_id, "field": "name"},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "📝 Description",
                    "value_edit_text",
                    {"mode": mode, "id": item_id, "field": "description"},
                ),
            ]
        ]
        if value is not None:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"💎 Focus: {'On' if value.active else 'Off'}",
                        "value_toggle_focus",
                        {"id": value.id},
                    )
                ]
            )
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        "Delete Value",
                        "value_delete_prompt",
                        {"id": value.id},
                    )
                ]
            )
        elif editor_values["name"].strip():
            rows.append(
                [
                    await token_button(
                        session, services.owner_id, "✅ Create Value", "value_create", {}
                    )
                ]
            )
        rows.append([await token_button(session, services.owner_id, "↩️ Back", "value_back", {})])
        await session.commit()

    body = (
        f"<b>{'Create Value' if mode == 'create' else 'Value'}</b>\n"
        f"Name: {html.escape(editor_values['name'] or '—')}\n"
        f"Description: {html.escape(editor_values['description'] or '—')}"
        + (
            f"\nLinked Cards: {carried_by[0]}\nLinked Checks: {carried_by[1]}"
            if mode == "view"
            else ""
        )
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


async def open_value(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    await render_value(message, services, mode="view", item_id=item_id, replace=replace)


async def render_value_text_prompt(
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
        state["flow"] = "value"
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title=f"Edit Value {field.title()}",
            current_value=str(state.get("values", {}).get(field, "")),
            instruction=f"Send the new {field}.",
            back_action="value_text_back",
            back_payload={"mode": mode, "id": item_id},
            related_id=item_id,
        ),
        state=state,
    )


def _validator(state: Mapping[str, Any]) -> TextValidator[str] | None:
    return required_text("Value name") if state["field"] == "name" else None


async def _apply_text(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: str
) -> None:
    """A Value being created is not saved yet, so the typed value stays in the editor."""
    del services
    if state["mode"] == "view":
        await update_value_fields(session, int(state["item_id"]), **{str(state["field"]): value})


async def _render_after_text(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    item_id = state.get("item_id")
    await render_value(
        message,
        services,
        mode=str(state["mode"]),
        item_id=int(item_id) if item_id is not None else None,
        values={**dict(state.get("values", {})), str(state["field"]): value},
        replace_message_id=int(state["text_input"]["message_id"]),
    )


TEXT_INPUT = TextInputFlow(
    name="value",
    validator=_validator,
    apply=_apply_text,
    render=_render_after_text,
)


async def _on_create_prompt(context: CallbackContext) -> None:
    await render_value(context.message, context.services, mode="create")


async def _on_view(context: CallbackContext) -> None:
    await render_value(
        context.message, context.services, mode="view", item_id=context.payload["id"]
    )


async def _on_edit_text(context: CallbackContext) -> None:
    await render_value_text_prompt(
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
    await render_value(
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
        value = await create_value(
            session, values.get("name", ""), values.get("description", "").strip() or None
        )
        await session.commit()
    await render_value(context.message, context.services, mode="view", item_id=value.id)


async def _on_toggle_focus(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await set_value_focus(session, context.payload["id"])
        await session.commit()
    await render_value(
        context.message, context.services, mode="view", item_id=context.payload["id"]
    )


async def _on_delete_prompt(context: CallbackContext) -> None:
    async with context.sessions() as session:
        value = await session.get(Value, context.payload["id"])
        if value is None:
            raise DomainError("Value does not exist")
        cards, checks = await value_link_counts(session, value.id)
        confirm = await token_button(
            session,
            context.owner_id,
            "Delete Value",
            "value_delete_confirm",
            {"id": value.id},
        )
        back = await token_button(
            session, context.owner_id, "↩️ Back", "value_view", {"id": value.id}
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Delete Value?</b>\n{html.escape(value.name)} will be deleted and taken off "
        f"{_carrier_phrase(cards, checks)}. Nothing it is on is deleted.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [back]]),
        related_id=value.id,
    )


def _carrier_phrase(cards: int, checks: int) -> str:
    """One wording for the delete question and for the receipt that answers it."""
    parts = [
        f"{count} {label}{'' if count == 1 else 's'}"
        for label, count in (("Card", cards), ("Check", checks))
        if count
    ]
    return " and ".join(parts) or "nothing"


async def _on_delete_confirm(context: CallbackContext) -> None:
    async with context.sessions() as session:
        value, removed = await delete_value(session, context.payload["id"])
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        back = await token_button(
            session, context.owner_id, "Back to Values", "value_back", {}
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Deleted <b>{html.escape(value.name)}</b>. Taken off {removed} link(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
        related_id=value.id,
    )


async def _on_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
    await command_values(context.message, context.services)


VALUE_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "value_create_prompt": _on_create_prompt,
    "value_view": _on_view,
    "value_edit_text": _on_edit_text,
    "value_text_back": _on_text_back,
    "value_create": _on_create,
    "value_toggle_focus": _on_toggle_focus,
    "value_delete_prompt": _on_delete_prompt,
    "value_delete_confirm": _on_delete_confirm,
    "value_back": _on_back,
}
