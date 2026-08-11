from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select

from ..domain import DomainError
from ..enums import MessageKind
from ..models import Check, CheckTag, CheckValue, Tag, UiSession, Value
from ._core import ITEM_REFERENCES, Services
from ._messaging import edit_registered_message, send_registered, token_button
from .cards import linked_card_count

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
) -> None:
    if entity not in ITEM_REFERENCES or mode not in {"create", "view"}:
        raise DomainError("Unsupported item editor")
    spec = ITEM_REFERENCES[entity]
    linked_count = 0
    async with services.sessions() as session:
        item: Tag | Value | None = None
        if mode == "view":
            item = await session.get(spec.model, item_id)
            if item is None or item.archived_at is not None:
                raise DomainError(f"{entity.title()} does not exist")
            editor_values = {"name": item.name, "description": item.description}
            linked_count = await linked_card_count(session, spec, item.id)
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
            check_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Check)
                    .where(
                        Check.archived_at.is_(None),
                        Check.id.in_(
                            select(CheckValue.check_id).where(CheckValue.value_id == item.id)
                            if entity == "value"
                            else select(CheckTag.check_id).where(CheckTag.tag_id == item.id)
                        ),
                    )
                )
                or 0
            )
            if check_count:
                rows.append(
                    [
                        await token_button(
                            session,
                            services.owner_id,
                            f"☑️ Checks ({check_count})",
                            "item_checks",
                            {
                                "scope": {"kind": entity, "id": item.id},
                                "back": {"kind": "home"},
                            },
                        )
                    ]
                )
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"Archive {entity.title()}",
                        "item_archive_prompt",
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
        + (f"\nLinked Cards: {linked_count}" if mode == "view" else "")
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
        state["message_id"] = message.message_id
        editor.kind = "item_text"
        editor.state = state
        back = await token_button(
            session,
            services.owner_id,
            "↩️ Back",
            "item_text_back",
            {"entity": entity, "mode": mode, "id": item_id},
        )
        await session.commit()
    current = str(state.get("values", {}).get(field, ""))
    await send_registered(
        message,
        services,
        f"<b>Current {html.escape(field)}</b>: {html.escape(current or '—')}\n\n"
        f"Set new {html.escape(field.title())}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
    )
