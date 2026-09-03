"""The screen a Card is written on by hand, and what each of its buttons does."""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....adapters.kinds import MessageKind
from ....foundation.errors import DomainError
from ....shell import (
    CallbackContext,
    CallbackHandler,
    Services,
    TextInputScreen,
    edit_registered_message,
    open_home,
    render_text_input,
    send_registered,
    token_button,
)
from ....shell.model import UiSession
from ...home.api import menu_markup
from ...planning.api import sprint_is_active
from ...tags.model import Tag
from ...values.model import Value
from ..model import CardKind
from ..use_cases import create_card
from .draft import (
    card_creation_errors,
    new_card_creation_state,
    require_card_draft,
    sanitize_card_creation_state,
)
from .presentation import card_overview_text
from .selectors import (
    CARD_DRAFT_CHOICE_FIELDS,
    CARD_DRAFT_RELATIONS,
    handle_card_creation_chooser,
)


async def card_creation_markup(
    session: AsyncSession, services: Services, state: dict[str, Any]
) -> InlineKeyboardMarkup:
    fields: list[tuple[str, str, dict[str, Any]]] = [
        ("🧩 Kind", "card_create_choose_kind", {}),
        ("✏️ Title", "card_create_edit_text", {"field": "title"}),
        ("📝 Note", "card_create_edit_text", {"field": "note"}),
        ("⚠️ Priority", "card_create_choose_priority", {}),
        ("⏱ Hard Time", "card_create_toggle", {"field": "hard_time"}),
    ]
    if state["kind"] == CardKind.ACTION.value:
        fields.insert(2, ("📍 Stage", "card_create_choose_stage", {}))
        fields.extend(
            [
                ("🚧 Blocked", "card_create_toggle", {"field": "blocked"}),
                ("🔢 Effort", "card_create_choose_effort", {}),
                ("🔁 Repeat", "card_create_toggle", {"field": "repeatable"}),
                ("🏷 Categories", "card_create_choose_categories", {}),
                ("⚡ Energy", "card_create_choose_energy", {}),
            ]
        )
        if state.get("blocked"):
            fields.append(
                (
                    "📝 Blocked reason",
                    "card_create_edit_text",
                    {"field": "blocked_description"},
                )
            )
    fields.extend(
        [
            ("💎 Values", "card_create_choose_values", {}),
            ("🏷 Tags", "card_create_choose_tags", {}),
        ]
    )
    buttons = [await token_button(session, services.owner_id, *field) for field in fields]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    if not card_creation_errors(state):
        rows.append([await token_button(session, services.owner_id, "✅ Save", "card_create_save")])
    rows.append(
        [await token_button(session, services.owner_id, "🗑 Discard", "card_create_discard")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_card_creation(
    message: Message,
    services: Services,
    *,
    replace_message_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(
                UiSession.owner_id == services.owner_id,
                UiSession.kind == "card_create",
            )
        )
        if editor is None:
            await send_registered(
                message,
                services,
                "Card creation is no longer active.",
                kind=MessageKind.ERROR,
                markup=menu_markup(
                    services.commands, sprint_active=await sprint_is_active(session)
                ),
            )
            return
        state = sanitize_card_creation_state(dict(editor.state or {}))
        editor.state = state
        value_ids = list(state["value_ids"])
        tag_ids = list(state["tag_ids"])
        values = (
            list(await session.scalars(select(Value).where(Value.id.in_(value_ids))))
            if value_ids
            else []
        )
        tags = (
            list(await session.scalars(select(Tag).where(Tag.id.in_(tag_ids)))) if tag_ids else []
        )
        display = {
            **state,
            "value_names": [value.name for value in values],
            "tag_names": [tag.name for tag in tags],
        }
        text = card_overview_text(display, heading="Create Card")
        errors = card_creation_errors(state)
        if errors:
            text += "\n\n" + "\n".join(f"⚠️ {html.escape(error)}" for error in errors)
        markup = await card_creation_markup(session, services, state)
        editor_id = editor.id
        await session.commit()
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.EDITOR,
            markup=markup,
            related_id=editor_id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.EDITOR,
            markup=markup,
            related_id=editor_id,
        )


async def start_manual_card_creation(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="card_create",
                state=new_card_creation_state(),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        await session.commit()
    await render_card_creation(message, services)


async def _on_view(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        if draft is None:
            raise DomainError("Card creation is no longer active")
        state = dict(draft.state or {})
        state.pop("input_field", None)
        state.pop("message_id", None)
        state.pop("text_input", None)
        state.pop("flow", None)
        draft.kind = "card_create"
        draft.state = sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_edit_text(context: CallbackContext) -> None:
    field = context.payload["field"]
    async with context.sessions() as session:
        draft = await require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        current = str(state.get(field) or "")
        state["input_field"] = field
        state["flow"] = "card_create"
    await render_text_input(
        context.message,
        context.services,
        screen=TextInputScreen(
            title=f"Edit Card {field.replace('_', ' ').title()}",
            current_value=current,
            instruction=f"Send the new {field.replace('_', ' ')}.",
            back_action="card_create_view",
            back_payload={},
        ),
        state=state,
    )


async def _on_toggle(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        field = context.payload["field"]
        state[field] = not bool(state.get(field))
        draft.state = sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_chooser(context: CallbackContext) -> None:
    await handle_card_creation_chooser(
        context.message,
        context.services,
        context.action,
        page=int(context.payload.get("page", 0)),
    )


async def _on_set(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        state[context.payload["field"]] = context.payload["value"]
        draft.state = sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_toggle_relation(context: CallbackContext) -> None:
    field, payload_key = CARD_DRAFT_RELATIONS[context.action]
    async with context.sessions() as session:
        draft = await require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        selected = set(state.get(field) or [])
        selected.symmetric_difference_update({context.payload[payload_key]})
        state[field] = sorted(selected)
        draft.state = sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_save(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await require_card_draft(session, context.owner_id)
        state = sanitize_card_creation_state(dict(draft.state or {}))
        errors = card_creation_errors(state)
        if errors:
            raise DomainError("Card is incomplete: " + "; ".join(errors))
        card = await create_card(
            session,
            kind=state["kind"],
            title=state["title"],
            note=state["note"],
            stage=state["stage"],
            priority=state["priority"],
            hard_time=state["hard_time"],
            blocked=state["blocked"],
            blocked_description=state["blocked_description"],
            effort_points=state["effort_points"],
            repeatable=state["repeatable"],
            categories=set(state["categories"]),
            energy_types=set(state["energy_types"]),
            value_ids=set(state["value_ids"]),
            tag_ids=set(state["tag_ids"]),
        )
        await session.delete(draft)
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"✅ Created <b>{html.escape(card.title)}</b>.",
        kind=MessageKind.DIALOGUE_ASSISTANT,
        related_id=card.id,
    )


async def _on_discard(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
    await open_home(context.message, context.services)


CARD_DRAFT_ACTIONS: dict[str, CallbackHandler] = {
    "card_create_view": _on_view,
    "card_create_edit_text": _on_edit_text,
    "card_create_toggle": _on_toggle,
    "card_create_set": _on_set,
    "card_create_save": _on_save,
    "card_create_discard": _on_discard,
    **{f"card_create_choose_{field}": _on_chooser for field in CARD_DRAFT_CHOICE_FIELDS},
    **dict.fromkeys(CARD_DRAFT_RELATIONS, _on_toggle_relation),
}
