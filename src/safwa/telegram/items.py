from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select

from ..constants import REQUEST_RESULT_LIMIT
from ..domain import DomainError
from ..enums import MessageKind
from ..features.saved_requests.use_cases import request_cards
from ..models import SavedRequest, Tag, UiSession, Value
from ._core import ITEM_REFERENCES, Services
from ._messaging import edit_registered_message, send_registered, token_button
from ._presentation import kind_label, menu_row
from .cards import carrier_counts
from .text_input import TextInputScreen, render_text_input

logger = logging.getLogger(__name__)



async def render_item_editor(
    message: Message,
    services: Services,
    entity: str,
    *,
    mode: str,
    item_id: int | None = None,
    values: dict[str, str] | None = None,
    replace_message_id: int | None = None,
    replace: bool | None = None,
) -> None:
    if entity not in ITEM_REFERENCES or mode not in {"create", "view"}:
        raise DomainError("Unsupported item editor")
    spec = ITEM_REFERENCES[entity]
    carried_by: list[tuple[str, int]] = []
    async with services.sessions() as session:
        item: Tag | Value | None = None
        if mode == "view":
            item = await session.get(spec.model, item_id)
            if item is None:
                raise DomainError(f"{entity.title()} does not exist")
            editor_values = {"name": item.name, "description": item.description}
            carried_by = await carrier_counts(session, spec, item.id)
        else:
            editor_values = {"name": "", "description": "", **(values or {})}

        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        state: dict[str, Any] = {
            "entity": entity,
            "mode": mode,
            "item_id": item_id,
            "values": editor_values,
            "message_id": replace_message_id or message.message_id,
        }
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="item_editor",
                state=state,
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        name = await token_button(
            session,
            services.owner_id,
            "✏️ Name",
            "item_edit_text",
            {"entity": entity, "mode": mode, "id": item_id, "field": "name"},
        )
        description = await token_button(
            session,
            services.owner_id,
            "📝 Description",
            "item_edit_text",
            {"entity": entity, "mode": mode, "id": item_id, "field": "description"},
        )
        rows: list[list[InlineKeyboardButton]] = [[name, description]]
        if entity == "value" and mode == "view" and isinstance(item, Value):
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"💎 Focus: {'On' if item.active else 'Off'}",
                        "item_toggle_focus",
                        {"id": item.id},
                    )
                ]
            )
        if mode == "create" and editor_values["name"].strip():
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"✅ Create {entity.title()}",
                        "item_create",
                        {"entity": entity},
                    )
                ]
            )
        if mode == "view" and item is not None:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"Delete {entity.title()}",
                        "item_delete_prompt",
                        {"entity": entity, "id": item.id},
                    )
                ]
            )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "item_back",
                    {"entity": entity},
                )
            ]
        )
        await session.commit()

    title = f"Create {entity.title()}" if mode == "create" else entity.title()
    body = (
        f"<b>{title}</b>\n"
        f"Name: {html.escape(editor_values['name'] or '—')}\n"
        f"Description: {html.escape(editor_values['description'] or '—')}"
        + (
            "".join(f"\nLinked {label}s: {count}" for label, count in carried_by)
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


async def render_saved_request(
    message: Message,
    services: Services,
    request_id: int,
    *,
    replace: bool | None = None,
) -> None:
    async with services.sessions() as session:
        request = await session.get(SavedRequest, request_id)
        if request is None:
            raise DomainError("Request no longer exists")
        matches = await request_cards(session, request.query_sql, services.views)
        cards = matches[:REQUEST_RESULT_LIMIT]
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"{kind_label(card.kind)} · {card.title}"[:60],
                    "card_view",
                    {"id": card.id, "back": {"kind": "request", "id": request.id}},
                )
            ]
            for card in cards
        ]
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↻ Refresh",
                    "request_view",
                    {"id": request.id},
                )
            ]
        )
        rows.append(menu_row())
        await session.commit()
    details = request.description or "No description."
    details += f"\n\n{len(matches)} matching card{'s' if len(matches) != 1 else ''}"
    if len(matches) > REQUEST_RESULT_LIMIT:
        details += f" (showing first {REQUEST_RESULT_LIMIT})"
    await send_registered(
        message,
        services,
        f"<b>{html.escape(request.name)}</b>\n{html.escape(details)}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=request.id,
        replace=replace,
    )


async def render_item_text_prompt(
    message: Message,
    services: Services,
    *,
    entity: str,
    mode: str,
    item_id: int | None,
    field: str,
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
        state["flow"] = "item"
    current = str(state.get("values", {}).get(field, ""))
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title=f"Edit {entity.title()} {field.title()}",
            current_value=current,
            instruction=f"Send the new {field}.",
            back_action="item_text_back",
            back_payload={"entity": entity, "mode": mode, "id": item_id},
            related_id=item_id,
        ),
        state=state,
    )
