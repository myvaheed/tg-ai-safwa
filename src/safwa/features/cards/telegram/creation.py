"""The manual Card draft: a `UiSession` row that persists nothing until Save."""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....enums import CardKind, MessageKind, Priority
from ....foundation.errors import DomainError
from ....models import Tag, UiSession, Value
from ....shell import (
    Services,
    edit_registered_message,
    menu_markup,
    send_registered,
    sprint_is_active,
    token_button,
)
from ..model import CardStage
from ..use_cases import validate_action_fields, validate_blocked_fields
from .presentation import card_overview_text


def _new_card_creation_state() -> dict[str, Any]:
    return {
        "kind": CardKind.ACTION.value,
        "title": "",
        "note": "",
        "stage": CardStage.BACKLOG.value,
        "priority": Priority.MEDIUM.value,
        "hard_time": False,
        "blocked": False,
        "blocked_description": "",
        "effort_points": None,
        "repeatable": False,
        "categories": [],
        "energy_types": [],
        "value_ids": [],
        "tag_ids": [],
    }


def sanitize_card_creation_state(state: dict[str, Any]) -> dict[str, Any]:
    clean = {**_new_card_creation_state(), **state}
    try:
        clean["kind"] = CardKind(clean["kind"]).value
    except ValueError:
        clean["kind"] = CardKind.ACTION.value
    try:
        clean["stage"] = CardStage(clean["stage"]).value
    except ValueError:
        clean["stage"] = CardStage.BACKLOG.value
    if clean["stage"] in {CardStage.DONE.value, CardStage.CANCELLED.value}:
        clean["stage"] = CardStage.BACKLOG.value
    if clean["kind"] != CardKind.ACTION.value:
        clean.update(
            stage=CardStage.BACKLOG.value,
            effort_points=None,
            repeatable=False,
            blocked=False,
            categories=[],
            energy_types=[],
        )
    if not clean["blocked"]:
        clean["blocked_description"] = ""
    for field in ("categories", "energy_types", "value_ids", "tag_ids"):
        clean[field] = list(dict.fromkeys(clean.get(field) or []))
    return clean


def card_creation_errors(state: dict[str, Any]) -> list[str]:
    """Report what still blocks Save, using the same rules the domain enforces.

    The draft is checked here only so Save can be hidden until it would succeed;
    ``create_card`` remains the authority and revalidates everything.
    """
    errors: list[str] = []
    if not str(state.get("title", "")).strip():
        errors.append("Add a title")
    for check in (
        lambda: validate_action_fields(
            state["kind"],
            state.get("effort_points"),
            bool(state.get("repeatable")),
            set(state.get("categories") or []),
            set(state.get("energy_types") or []),
            blocked=bool(state.get("blocked")),
        ),
        lambda: validate_blocked_fields(
            bool(state.get("blocked")), state.get("blocked_description")
        ),
    ):
        try:
            check()
        except DomainError as error:
            errors.append(str(error))
    return errors


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
                markup=menu_markup(sprint_active=await sprint_is_active(session)),
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
            kind=MessageKind.CARD_EDITOR,
            markup=markup,
            related_id=editor_id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.CARD_EDITOR,
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
                state=_new_card_creation_state(),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        await session.commit()
    await render_card_creation(message, services)


async def require_card_draft(session: AsyncSession, owner_id: int) -> UiSession:
    draft = await session.scalar(
        select(UiSession).where(
            UiSession.owner_id == owner_id,
            UiSession.kind == "card_create",
        )
    )
    if draft is None:
        raise DomainError("Card creation is no longer active")
    return draft


async def card_editor_back_state(session: AsyncSession, owner_id: int) -> dict[str, Any]:
    """Keep the navigation trail of the Card screen a focused prompt replaces."""
    editor = await session.scalar(select(UiSession).where(UiSession.owner_id == owner_id))
    if editor is None or editor.kind != "card_editor":
        return {}
    return dict(editor.state.get("back", {}))
