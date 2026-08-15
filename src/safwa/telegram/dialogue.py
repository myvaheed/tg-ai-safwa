from __future__ import annotations

import html
import logging
from datetime import UTC, datetime

from aiogram import F
from aiogram.enums import ChatAction
from aiogram.types import Message
from sqlalchemy import delete, select

from ..domain import (
    DomainError,
    edit_card_text,
    set_sprint_success_criteria,
    update_card_fields,
    update_profile,
    update_reminder_text,
    update_tag_fields,
    update_value_fields,
)
from ..enums import MessageKind
from ..history import HistoryEntry, register_message
from ..models import UiSession, Workspace
from ._core import BACKGROUND_SOURCE_ID, Services, router
from ._messaging import (
    clear_message_markup,
    delete_text_input,
    dismiss_prior_ui,
    materialize_queued_dialogue,
    send_registered,
    send_summary,
)
from .cards import render_card, render_card_creation, sanitize_card_creation_state
from .commands import SETTINGS_FIELDS, command_settings, render_settings_field_prompt
from .items import render_item_editor
from .proposals import render_ai_outcome
from .reminders import render_reminder
from .sprint import render_sprint_confirm

logger = logging.getLogger(__name__)


@router.message(F.text)
async def ordinary_text(message: Message, services: Services) -> None:
    if not message.text or message.text.startswith("/"):
        return
    async with services.sessions() as session:
        ui = await session.scalar(
            select(UiSession)
            .where(
                UiSession.owner_id == services.owner_id, UiSession.expires_at > datetime.now(UTC)
            )
            .order_by(UiSession.created_at.desc())
        )
        ui_kind = ui.kind if ui is not None else None
        ui_state = dict(ui.state) if ui is not None else {}

    if ui_kind == "item_text":
        entity = str(ui_state["entity"])
        mode = str(ui_state["mode"])
        field = str(ui_state["field"])
        item_id = ui_state.get("item_id")
        message_id = int(ui_state["message_id"])
        values = dict(ui_state.get("values", {}))
        values[field] = message.text.strip()
        async with services.sessions() as session:
            if mode == "view":
                if entity == "value":
                    await update_value_fields(session, int(item_id), **{field: values[field]})
                else:
                    await update_tag_fields(session, int(item_id), **{field: values[field]})
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, message_id)
        await render_item_editor(
            message,
            services,
            entity,
            mode=mode,
            item_id=int(item_id) if item_id is not None else None,
            values=values,
            replace_message_id=message_id if input_deleted else None,
        )
        return
    if ui_kind == "reminder_text":
        reminder_id = int(ui_state["reminder_id"])
        message_id = int(ui_state["message_id"])
        async with services.sessions() as session:
            await update_reminder_text(session, reminder_id, message.text)
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, message_id)
        await render_reminder(message, services, reminder_id)
        return
    if ui_kind == "sprint_criteria":
        message_id = int(ui_state["message_id"])
        async with services.sessions() as session:
            await set_sprint_success_criteria(session, message.text)
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        await delete_text_input(message, services)
        # The prompt stays in the chat as its own turn, so only its buttons must go.
        await clear_message_markup(message, message_id)
        await render_sprint_confirm(message, services)
        return
    if ui_kind == "settings_field":
        field_name = str(ui_state["field"])
        await delete_text_input(message, services)
        await clear_message_markup(message, int(ui_state["message_id"]))
        field = SETTINGS_FIELDS.get(field_name)
        if field is None:
            await command_settings(message, services)
            return
        try:
            value = field.parse(message.text.strip())
        except ValueError as error:
            await render_settings_field_prompt(
                message, services, field_name, notice=str(error)
            )
            return
        async with services.sessions() as session:
            await update_profile(session, **{field_name: value})
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        await command_settings(message, services, notice=f"{field.title} updated.")
        return
    if ui_kind == "card_create_text":
        async with services.sessions() as session:
            editor = await session.scalar(
                select(UiSession).where(UiSession.owner_id == services.owner_id)
            )
            if editor is None:
                raise DomainError("Card creation is no longer active")
            state = dict(editor.state or {})
            field = str(state.pop("input_field"))
            message_id = int(state.pop("message_id"))
            state[field] = message.text.strip()
            editor.kind = "card_create"
            editor.state = sanitize_card_creation_state(state)
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, message_id)
        await render_card_creation(
            message,
            services,
            replace_message_id=message_id if input_deleted else None,
        )
        return
    if ui_kind == "card_text":
        async with services.sessions() as session:
            value = message.text.strip()
            card = await edit_card_text(session, ui_state["card_id"], ui_state["field"], value)
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, int(ui_state["message_id"]))
        await render_card(
            message,
            services,
            card.id,
            replace_message_id=int(ui_state["message_id"]) if input_deleted else None,
            back=dict(ui_state.get("back", {})),
        )
        return
    if ui_kind == "card_blocked_text":
        async with services.sessions() as session:
            card = await update_card_fields(
                session,
                ui_state["card_id"],
                {
                    "blocked": True,
                    "blocked_description": message.text.strip(),
                },
            )
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, int(ui_state["message_id"]))
        await render_card(
            message,
            services,
            card.id,
            replace_message_id=int(ui_state["message_id"]) if input_deleted else None,
            back=dict(ui_state.get("back", {})),
        )
        return
    await dismiss_prior_ui(message, services)
    async with services.sessions() as session:
        await register_message(
            session, message.chat.id, message.message_id, "in", MessageKind.DIALOGUE_USER
        )
        await session.commit()
    source = HistoryEntry(
        message_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        role="user",
        text=message.text,
        created_at=message.date.astimezone(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    dialogue_revision = services.guard.dialogue_revision
    await services.guard.acquire(message.message_id, queue_messages=True)
    current_source = source
    current_request = message.text
    try:
        while True:
            async with services.sessions() as session:
                workspace = await session.get(Workspace, 1)
                starting_workspace_revision = workspace.revision
            await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            dialogue = await services.history.dialogue(
                message.chat.id, source_message=current_source
            )
            outcome = await services.advisor.handle(
                current_request,
                source_message_id=current_source.message_id,
                dialogue=dialogue,
            )
            async with services.sessions() as session:
                workspace = await session.get(Workspace, 1)
                if (
                    services.guard.dialogue_revision != dialogue_revision
                    or workspace.revision != starting_workspace_revision
                ):
                    return
            await render_ai_outcome(message, services, outcome)
            queued = await materialize_queued_dialogue(message, services)
            if queued is None:
                break
            queued_message, current_request = queued
            await dismiss_prior_ui(queued_message, services)
            current_source = HistoryEntry(
                message_id=queued_message.message_id,
                sender_id=services.owner_id,
                role="user",
                text=current_request,
                created_at=queued_message.date.astimezone(UTC),
                kind=MessageKind.DIALOGUE_USER.value,
            )

        services.guard.release(message.message_id)

        if services.guard.reserve_background():
            summary_revision = services.guard.dialogue_revision
            try:
                await services.continuity.maybe_summarize(
                    message.chat.id,
                    lambda text, covered_id: send_summary(
                        message, services, text, covered_id
                    ),
                    still_current=lambda: (
                        services.guard.background
                        and services.guard.dialogue_revision == summary_revision
                    ),
                )
            finally:
                services.guard.release(BACKGROUND_SOURCE_ID)
    except Exception as error:
        logger.exception("Could not handle ordinary text")
        await send_registered(
            message,
            services,
            "Safwa could not complete that request. Your planning data was not changed.\n"
            + html.escape(str(error)),
            kind=MessageKind.ERROR,
        )
    finally:
        if services.guard.active_source_id == message.message_id:
            try:
                await materialize_queued_dialogue(message, services)
            except Exception:
                logger.exception("Could not restore queued messages after generation stopped")
        services.guard.release(message.message_id)
