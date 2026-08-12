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
    create_check,
    edit_card_text,
    toggle_card_check,
    update_card_fields,
    update_check_fields,
    update_tag_fields,
    update_value_fields,
)
from ..enums import MessageKind
from ..history import HistoryBoundaryMissing, HistoryEntry, mark_kind, register_message
from ..memory import estimate_tokens
from ..models import SummaryState, UiSession, Workspace
from ._core import Services, router
from ._messaging import (
    clear_message_markup,
    delete_text_input,
    dismiss_prior_ui,
    send_registered,
)
from .cards import render_card, render_card_creation, sanitize_card_creation_state
from .checks import render_check
from .items import render_item_editor
from .proposals import render_ai_outcome

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
    if ui_kind == "check_text":
        check_id = ui_state.get("check_id")
        field = str(ui_state["field"])
        card_id = int(ui_state["card_id"])
        back = dict(ui_state.get("back") or {"kind": "home"})
        message_id = int(ui_state["message_id"])
        typed = message.text.strip()
        async with services.sessions() as session:
            if check_id is None:
                created = await create_check(session, title=typed)
                await toggle_card_check(session, card_id, created.id)
                check_id = created.id
            else:
                await update_check_fields(session, int(check_id), {field: typed})
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, message_id)
        await render_check(
            message,
            services,
            int(check_id),
            card_id=card_id,
            back=back,
            replace_message_id=message_id if input_deleted else None,
        )
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
        workspace = await session.get(Workspace, 1)
        starting_workspace_revision = workspace.revision
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
    await services.guard.acquire(message.message_id)
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        dialogue = await services.history.dialogue(message.chat.id, source_message=source)
        outcome = await services.advisor.handle(
            message.text,
            source_message_id=message.message_id,
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
        # The foreground response is now visible. Release its lease before any
        # optional continuity work so proposal callbacks and new dialogue are not
        # rejected while summary generation is running.
        services.guard.release(message.message_id)

        async def send_summary(text: str, covered_id: int) -> None:
            sent = await message.answer(mark_kind(html.escape(text), MessageKind.SUMMARY))
            async with services.sessions() as session:
                await register_message(
                    session,
                    sent.chat.id,
                    sent.message_id,
                    "out",
                    MessageKind.SUMMARY,
                )
                state = await session.get(SummaryState, 1)
                if state is None:
                    state = SummaryState(id=1)
                    session.add(state)
                state.summary_message_id = sent.message_id
                state.covered_message_id = covered_id
                state.estimated_tokens = estimate_tokens(text)
                await session.commit()

        await services.continuity.maybe_summarize(message.chat.id, send_summary)
    except HistoryBoundaryMissing as error:
        await send_registered(
            message,
            services,
            html.escape(str(error)),
            kind=MessageKind.ERROR,
        )
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
        services.guard.release(message.message_id)
