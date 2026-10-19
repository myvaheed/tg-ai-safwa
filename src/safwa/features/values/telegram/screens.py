"""The Values the owner keeps: the list, one Value, and what a tap on it does."""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.proposals.api import EditableField
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    TextInputScreen,
    TextValidator,
    back_button,
    choice_rows,
    edit_registered_message,
    edited_screen,
    markdown_field,
    menu_row,
    paginate,
    place_button,
    render_citations,
    render_text_input,
    required_text,
    send_registered,
)
from tg_agent_shell.telegram.contributions import TextInputFlow
from tg_agent_shell.telegram.layout import PAGE_SIZE
from tg_agent_shell.telegram.model import UiSession

from ....constants import DESCRIPTION_MAX_CHARS
from ...cards.telegram import card_results, cards_link
from ...checks.telegram import check_results, linked_check_links
from ..model import Value
from ..use_cases import (
    create_value,
    delete_value,
    update_value_fields,
    value_cards,
    value_checks,
    value_description,
    value_link_counts,
)

_EDITOR_TTL = timedelta(minutes=30)


# The Values list, which the menu opens and every Value opened from it goes back to.
_LIST = Place("value_list")


async def command_values(
    message: Message,
    services: Services,
    *,
    page: int = 0,
    prioritizing: bool = False,
    selected_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        values = list(await session.scalars(select(Value).order_by(Value.order)))
        current = paginate(values, page)
        normal = _LIST.but(page=current.index) if current.index else _LIST
        selected = (
            next((value for value in values if value.id == selected_id), None)
            if prioritizing
            else None
        )
        here = (
            _LIST.but(
                prioritizing=True, page=current.index, selected_id=selected.id if selected else None
            )
            if prioritizing
            else normal
        )
        options = [(f"{value.order}. 💎 {value.name}", value.id) for value in current.items]
        choices = choice_rows(
            options,
            {selected.id} if selected else set(),
            lambda value_id: (
                here.but(selected_id=None if selected and selected.id == value_id else value_id)
                if prioritizing
                else here.child("value_view", id=value_id)
            ),
        )
        rows = [
            [await place_button(session, services.owner_id, text, place)] for text, place in choices
        ]
        if selected is not None:
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        text,
                        here.child("value_order", id=selected.id, offset=offset),
                    )
                    for text, offset in (("↑", -1), ("↓", 1))
                ]
            )
        if prioritizing or current.count > 1:
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        text,
                        here.but(page=max(0, min(current.count - 1, current.index + offset))),
                    )
                    for text, offset in (("←", -1), ("→", 1))
                ]
            )
        if prioritizing:
            rows.append([await back_button(session, services.owner_id, normal)])
        else:
            if values:
                rows.append(
                    [
                        await place_button(
                            session,
                            services.owner_id,
                            "↕️ Prioritize",
                            here.but(prioritizing=True, selected_id=None),
                        )
                    ]
                )
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        "➕ Add Value",
                        here.child("value_create_prompt"),
                    )
                ]
            )
            rows.append(menu_row())
        await session.commit()
    title = "💎 Values" + (" · Prioritization" if prioritizing else "")
    if prioritizing or current.count > 1:
        title += f" · {current.label}"
    description = "A Value is in focus while a Card in Today carries it."
    if prioritizing:
        description = (
            f"Selected: {html.escape(selected.name)} · position {selected.order}."
            if selected
            else "No Value selected."
        )
    await send_registered(
        message,
        services,
        f"<b>{title}</b>\n{description}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def render_value(
    message: Message,
    services: Services,
    *,
    mode: str,
    item_id: int | None = None,
    back: Place | None = None,
    values: dict[str, str] | None = None,
    replace_message_id: int | None = None,
    replace: bool | None = None,
) -> None:
    """One Value, or one being created. `back` is where it was opened from."""
    if mode not in {"create", "view"}:
        raise DomainError("Unsupported Value editor")
    here = Place("value_view", {"id": item_id}, back)
    linked = ""
    async with services.sessions() as session:
        value: Value | None = None
        if mode == "view":
            value = await session.get(Value, item_id)
            if value is None:
                raise DomainError("Value does not exist")
            editor_values = {"name": value.name, "description": value.description}
            checks = await linked_check_links(
                session,
                services,
                await value_checks(session, value.id),
                here,
                here.child("value_checks", id=value.id),
            )
            cards = await cards_link(
                session,
                services,
                await value_cards(session, value.id),
                here.child("value_cards", id=value.id),
            )
            linked = f"\n<i>Linked Cards:</i> {cards}\n<i>Linked Checks:</i>\n" + ("\n".join(checks) or "—")
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
                await place_button(
                    session,
                    services.owner_id,
                    "✏️ Name",
                    Place("value_edit_text", {"mode": mode, "id": item_id, "field": "name"}, back),
                ),
                await place_button(
                    session,
                    services.owner_id,
                    "📝 Description",
                    Place(
                        "value_edit_text",
                        {"mode": mode, "id": item_id, "field": "description"},
                        back,
                    ),
                ),
            ]
        ]
        if value is not None:
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        "Delete Value",
                        Place("value_delete_prompt", {"id": value.id}, back),
                    )
                ]
            )
        elif editor_values["name"].strip() and editor_values["description"].strip():
            rows.append(
                [
                    await place_button(
                        session, services.owner_id, "✅ Create Value", Place("value_create", {}, back)
                    )
                ]
            )
        rows.append([await back_button(session, services.owner_id, back)])
        description = markdown_field("Description", editor_values["description"])
        if value is not None:
            description = await render_citations(session, services, description, here=here)
        else:
            description = await render_citations(session, services, description, plain=True)
        await session.commit()

    body = (
        f"<b>{'Create Value' if mode == 'create' else 'Value'}</b>\n"
        f"<i>Name:</i> {html.escape(editor_values['name'] or '—')}\n"
        f"\n{description}\n"
        + (linked or "\nA Value is created once it has a name and a description.")
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


async def render_value_cards(
    message: Message, services: Services, value_id: int, *, back: Place | None = None
) -> None:
    """The Cards carrying one Value, as a result. `back` is that Value's screen."""
    here = Place("value_cards", {"id": value_id}, back)
    async with services.sessions() as session:
        value = await session.get(Value, value_id)
        if value is None:
            raise DomainError("Value does not exist")
        cards = await value_cards(session, value.id)
        count, markup = await card_results(session, services, cards, here, back)
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>Linked Cards of {html.escape(value.name)}</b>\n\n{count}",
        kind=MessageKind.DASHBOARD,
        markup=markup,
        related_id=value.id,
    )


async def render_value_checks(
    message: Message, services: Services, value_id: int, *, back: Place | None = None, page: int = 0
) -> None:
    """All Checks carrying one Value, as a result."""
    here = Place("value_checks", {"id": value_id}, back)
    async with services.sessions() as session:
        value = await session.get(Value, value_id)
        if value is None:
            raise DomainError("Value does not exist")
        checks = await value_checks(session, value.id)
        body, markup = await check_results(session, services, checks, here, page=page)
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>Linked Checks of {html.escape(value.name)}</b>\n\n{body}",
        kind=MessageKind.DASHBOARD,
        markup=markup,
        related_id=value.id,
    )


async def open_value(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    await render_value(message, services, mode="view", item_id=item_id, replace=replace)


async def render_value_text_prompt(
    message: Message,
    services: Services,
    *,
    mode: str,
    item_id: int | None,
    field: str,
    back: Place | None = None,
) -> None:
    """Type one field of the Value on screen. `back` is where that Value was opened from."""
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
            instruction=_INSTRUCTIONS[field],
            markdown=field == "description",
            back=Place("value_text_back", {"mode": mode, "id": item_id}, back),
            related_id=item_id,
        ),
        state=state,
    )


_INSTRUCTIONS = {
    "name": "Send the new name.",
    "description": (
        "Send why this Value matters to you and what you do for it on an ordinary day, "
        f"at most {DESCRIPTION_MAX_CHARS} characters."
    ),
}


def _validator(state: Mapping[str, Any]) -> TextValidator[str] | None:
    return required_text("Value name") if state["field"] == "name" else value_description


# What Edit offers on a Value proposal: this screen's own fields (VL-EDIT-030).
VALUE_REVIEW_FIELDS = {
    "name": EditableField(
        "✏️ Name",
        "Edit Value Name",
        parse=required_text("Value name"),
        instruction=_INSTRUCTIONS["name"],
    ),
    "description": EditableField(
        "📝 Description",
        "Edit Value Description",
        parse=value_description,
        instruction=_INSTRUCTIONS["description"],
        markdown=True,
    ),
}


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
        back=edited_screen(state).back,
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
    await render_value(context.message, context.services, mode="create", back=context.back)


async def _on_view(context: CallbackContext) -> None:
    await render_value(
        context.message,
        context.services,
        mode="view",
        item_id=context.payload["id"],
        back=context.back,
    )


async def _on_cards(context: CallbackContext) -> None:
    await render_value_cards(
        context.message, context.services, context.payload["id"], back=context.back
    )


async def _on_edit_text(context: CallbackContext) -> None:
    await render_value_text_prompt(
        context.message,
        context.services,
        mode=context.payload["mode"],
        item_id=context.payload.get("id"),
        field=context.payload["field"],
        back=context.back,
    )


async def _on_checks(context: CallbackContext) -> None:
    await render_value_checks(
        context.message,
        context.services,
        context.payload["id"],
        back=context.back,
        page=int(context.payload.get("page", 0)),
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
        back=context.back,
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
        value = await create_value(session, values.get("name", ""), values.get("description", ""))
        await session.commit()
    await render_value(
        context.message, context.services, mode="view", item_id=value.id, back=context.back
    )


async def _on_delete_prompt(context: CallbackContext) -> None:
    async with context.sessions() as session:
        value = await session.get(Value, context.payload["id"])
        if value is None:
            raise DomainError("Value does not exist")
        cards, checks = await value_link_counts(session, value.id)
        confirm = await place_button(
            session,
            context.owner_id,
            "Delete Value",
            Place("value_delete_confirm", {"id": value.id}, context.back),
        )
        back = await back_button(
            session, context.owner_id, Place("value_view", {"id": value.id}, context.back)
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
        back = await back_button(session, context.owner_id, context.back)
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Deleted <b>{html.escape(value.name)}</b>. Taken off {removed} link(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
        related_id=value.id,
    )


async def _on_list(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
    await command_values(
        context.message,
        context.services,
        page=context.payload.get("page", 0),
        prioritizing=context.payload.get("prioritizing", False),
        selected_id=context.payload.get("selected_id"),
    )


async def _on_order(context: CallbackContext) -> None:
    async with context.sessions() as session:
        value = await session.get(Value, context.payload["id"])
        if value is None:
            raise DomainError("Value does not exist")
        count = len(list(await session.scalars(select(Value.id))))
        order = max(1, min(count, value.order + context.payload["offset"]))
        if order != value.order:
            await update_value_fields(session, value.id, order=order)
        value_id = value.id
        await session.commit()
    await command_values(
        context.message,
        context.services,
        page=(order - 1) // PAGE_SIZE,
        prioritizing=True,
        selected_id=value_id,
    )


VALUE_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "value_create_prompt": _on_create_prompt,
    "value_view": _on_view,
    "value_cards": _on_cards,
    "value_checks": _on_checks,
    "value_edit_text": _on_edit_text,
    "value_text_back": _on_text_back,
    "value_create": _on_create,
    "value_delete_prompt": _on_delete_prompt,
    "value_delete_confirm": _on_delete_confirm,
    "value_list": _on_list,
    "value_order": _on_order,
}
