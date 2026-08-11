from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.service import ProposalService
from ..domain import (
    DomainError,
    StaleStateError,
    archive_check,
    archive_subtree,
    archive_tag,
    archive_value,
    create_card,
    create_tag,
    create_value,
    delete_subtree,
    finish_action,
    finish_sprint,
    move_card,
    pending_checks,
    resolve_check,
    set_feedback,
    set_value_focus,
    start_sprint,
    update_card_fields,
    update_check_fields,
)
from ..enums import CardStage, MessageKind, ProposalStatus
from ..models import (
    CallbackToken,
    Card,
    ChangeProposal,
    Check,
    ProposalChange,
    UiSession,
    UserProfile,
)
from ._core import (
    CARD_CHOICE_FIELDS,
    CARD_DRAFT_CHOICE_FIELDS,
    CARD_DRAFT_RELATIONS,
    CARD_RELATION_TOGGLES,
    ITEM_REFERENCES,
    RELATION_CHOICES,
    CallbackContext,
    CallbackHandler,
    Services,
    router,
)
from ._messaging import send_registered, token_button
from ._presentation import menu_row, with_notice
from .cards import (
    card_creation_errors,
    card_editor_back_state,
    handle_card_creation_chooser,
    linked_card_count,
    render_card,
    render_card_choices,
    render_card_creation,
    render_children,
    render_dashboard,
    require_card_draft,
    sanitize_card_creation_state,
)
from .checks import (
    next_outcome,
    render_check,
    render_check_resolution,
    render_check_text_prompt,
    render_checks,
)
from .commands import (
    command_start,
    command_tags,
    command_values,
    end_subsession,
    render_feedback,
    render_saved_request,
)
from .items import render_item_editor, render_item_text_prompt
from .proposals import continue_agent_approval, render_proposal

logger = logging.getLogger(__name__)


async def _clear_ui_sessions(session: AsyncSession, owner_id: int) -> None:
    await session.execute(delete(UiSession).where(UiSession.owner_id == owner_id))


# --- Tag and Value item screens ------------------------------------------------


async def _on_item_create_prompt(context: CallbackContext) -> None:
    entity = "value" if context.action == "value_create_prompt" else "tag"
    await render_item_editor(context.message, context.services, entity, mode="create")


async def _on_item_view(context: CallbackContext) -> None:
    await render_item_editor(
        context.message,
        context.services,
        context.payload["entity"],
        mode="view",
        item_id=context.payload["id"],
    )


async def _on_item_edit_text(context: CallbackContext) -> None:
    await render_item_text_prompt(
        context.message,
        context.services,
        entity=context.payload["entity"],
        mode=context.payload["mode"],
        item_id=context.payload.get("id"),
        field=context.payload["field"],
    )


async def _on_item_text_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        values = dict(editor.state.get("values", {})) if editor is not None else {}
    await render_item_editor(
        context.message,
        context.services,
        context.payload["entity"],
        mode=context.payload["mode"],
        item_id=context.payload.get("id"),
        values=values,
    )


async def _on_item_create(context: CallbackContext) -> None:
    async with context.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        if editor is None or editor.kind != "item_editor":
            raise DomainError("Item editor expired")
        values = dict(editor.state.get("values", {}))
        create = create_value if context.payload["entity"] == "value" else create_tag
        item = await create(session, values.get("name", ""), values.get("description", ""))
        await session.commit()
    await render_item_editor(
        context.message,
        context.services,
        context.payload["entity"],
        mode="view",
        item_id=item.id,
    )


async def _on_item_toggle_focus(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await set_value_focus(session, context.payload["id"])
        await session.commit()
    await render_item_editor(
        context.message,
        context.services,
        "value",
        mode="view",
        item_id=context.payload["id"],
    )


async def _on_item_archive_prompt(context: CallbackContext) -> None:
    entity = context.payload["entity"]
    spec = ITEM_REFERENCES[entity]
    async with context.sessions() as session:
        item = await session.get(spec.model, context.payload["id"])
        if item is None or item.archived_at is not None:
            raise DomainError(f"{entity.title()} does not exist")
        linked_count = await linked_card_count(session, spec, item.id)
        confirm = await token_button(
            session,
            context.owner_id,
            f"Archive {entity.title()}",
            "item_archive_confirm",
            {"entity": entity, "id": item.id},
        )
        back = await token_button(
            session,
            context.owner_id,
            "↩️ Back",
            "item_view",
            {"entity": entity, "id": item.id},
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Archive {html.escape(entity.title())}?</b>\n"
        f"{html.escape(item.name)} will be removed from active lists and unlinked from "
        f"{linked_count} Card(s).",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [back]]),
        related_id=item.id,
    )


async def _on_item_archive_confirm(context: CallbackContext) -> None:
    entity = context.payload["entity"]
    async with context.sessions() as session:
        archive = archive_tag if entity == "tag" else archive_value
        item, linked_count = await archive(session, context.payload["id"])
        await _clear_ui_sessions(session, context.owner_id)
        back = await token_button(
            session,
            context.owner_id,
            f"Back to {entity.title()}s",
            "item_back",
            {"entity": entity},
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Archived <b>{html.escape(item.name)}</b>. Removed {linked_count} Card link(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
        related_id=item.id,
    )


async def _on_item_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
    listing = command_values if context.payload["entity"] == "value" else command_tags
    await listing(context.message, context.services)


async def _on_request_view(context: CallbackContext) -> None:
    await render_saved_request(context.message, context.services, context.payload["id"])


# --- Subsessions ---------------------------------------------------------------


async def _on_subsession_confirm(context: CallbackContext) -> None:
    await send_registered(
        context.message,
        context.services,
        "<b>Compressing subsession…</b>",
        kind=MessageKind.APPROVAL,
    )
    try:
        await end_subsession(
            context.message,
            context.services,
            start_message_id=int(context.payload["start_message_id"]),
            instruction=str(context.payload.get("instruction", "")),
        )
    except Exception:
        logger.exception("Could not end Safwa subsession")
        await send_registered(
            context.message,
            context.services,
            "Safwa could not compress the subsession. The branch was not deleted.",
            kind=MessageKind.ERROR,
            markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
        )


async def _on_subsession_cancel(context: CallbackContext) -> None:
    await send_registered(
        context.message,
        context.services,
        "Subsession end cancelled. The branch is unchanged.",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


# --- Transient manual Card draft -----------------------------------------------


async def _on_card_draft_view(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        if draft is None:
            raise DomainError("Card creation is no longer active")
        state = dict(draft.state or {})
        state.pop("input_field", None)
        state.pop("message_id", None)
        draft.kind = "card_create"
        draft.state = sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_card_draft_edit_text(context: CallbackContext) -> None:
    field = context.payload["field"]
    async with context.sessions() as session:
        draft = await require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        current = str(state.get(field) or "")
        state.update(input_field=field, message_id=context.message.message_id)
        draft.kind = "card_create_text"
        draft.state = state
        back = await token_button(session, context.owner_id, "↩️ Back", "card_create_view")
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Current {html.escape(field.replace('_', ' '))}</b>: "
        f"{html.escape(current or '—')}\n\n"
        f"Set new {html.escape(field.replace('_', ' ').title())}",
        kind=MessageKind.CARD_EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
    )


async def _on_card_draft_toggle(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        field = context.payload["field"]
        state[field] = not bool(state.get(field))
        draft.state = sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_card_draft_chooser(context: CallbackContext) -> None:
    await handle_card_creation_chooser(
        context.message,
        context.services,
        context.action,
        page=int(context.payload.get("page", 0)),
    )


async def _on_card_draft_set(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        state[context.payload["field"]] = context.payload["value"]
        draft.state = sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_card_draft_toggle_relation(context: CallbackContext) -> None:
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


async def _on_card_draft_save(context: CallbackContext) -> None:
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


async def _on_card_draft_discard(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
    await command_start(context.message, context.services)


# --- Navigation ----------------------------------------------------------------


async def _on_dashboard_page(context: CallbackContext) -> None:
    await render_dashboard(
        context.message,
        context.services,
        CardStage(context.payload["stage"]),
        title=context.payload["title"],
        page=int(context.payload["page"]),
    )


async def _on_card_view(context: CallbackContext) -> None:
    await render_card(
        context.message,
        context.services,
        context.payload["id"],
        back=context.payload.get("back"),
    )


async def _on_card_children(context: CallbackContext) -> None:
    await render_children(
        context.message,
        context.services,
        context.payload["id"],
        page=int(context.payload.get("page", 0)),
        back=context.payload.get("back"),
    )


async def _on_card_back(context: CallbackContext) -> None:
    back = context.payload.get("back") or {"kind": "home"}
    if back["kind"] == "dashboard":
        await render_dashboard(
            context.message,
            context.services,
            CardStage(back["stage"]),
            title=back["title"],
            page=int(back.get("page", 0)),
        )
    elif back["kind"] == "request":
        await render_saved_request(context.message, context.services, int(back["id"]))
    elif back["kind"] == "card":
        await render_card(
            context.message,
            context.services,
            int(back["id"]),
            back=back.get("back"),
        )
    elif back["kind"] == "children":
        await render_children(
            context.message,
            context.services,
            int(back["id"]),
            page=int(back.get("page", 0)),
            back=back.get("back"),
        )
    else:
        await command_start(context.message, context.services)


# --- Committed Card mutations --------------------------------------------------


async def _on_card_move(context: CallbackContext) -> None:
    async with context.sessions() as session:
        result = await move_card(
            session, context.payload["id"], CardStage(context.payload["stage"])
        )
        await session.commit()
    await render_card(
        context.message,
        context.services,
        context.payload["id"],
        notice="⚠️ " + "; ".join(result.warnings) if result.warnings else None,
    )


async def _on_card_edit_text(context: CallbackContext) -> None:
    card_id = context.payload["id"]
    field = context.payload["field"]
    async with context.sessions() as session:
        back_state = await card_editor_back_state(session, context.owner_id)
        await _clear_ui_sessions(session, context.owner_id)
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        session.add(
            UiSession(
                owner_id=context.owner_id,
                kind="card_text",
                state={
                    "card_id": card_id,
                    "field": field,
                    "message_id": context.message.message_id,
                    "back": back_state,
                },
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        back = await token_button(
            session,
            context.owner_id,
            "↩️ Back",
            "card_view",
            {"id": card_id, "back": back_state},
        )
        current = str(getattr(card, field) or "")
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Current {html.escape(field)}</b>: "
        f"{html.escape(current or '—')}\n\n"
        f"Set new {html.escape(field.title())}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
        related_id=card_id,
    )


async def _on_card_choices(context: CallbackContext) -> None:
    await render_card_choices(
        context.message,
        context.services,
        context.action,
        context.payload["id"],
        page=int(context.payload.get("page", 0)),
    )


async def _on_card_set_field(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await update_card_fields(
            session,
            context.payload["id"],
            {context.payload["field"]: context.payload["value"]},
        )
        await session.commit()
    await render_card(context.message, context.services, context.payload["id"])


async def _on_card_toggle_field(context: CallbackContext) -> None:
    field = context.payload["field"]
    async with context.sessions() as session:
        card = await session.get(Card, context.payload["id"])
        if card is None:
            raise DomainError("Card does not exist")
        if field == "blocked" and not card.blocked:
            # Blocking always needs a reason, so ask for it before writing anything.
            back_state = await card_editor_back_state(session, context.owner_id)
            await _clear_ui_sessions(session, context.owner_id)
            session.add(
                UiSession(
                    owner_id=context.owner_id,
                    kind="card_blocked_text",
                    state={
                        "card_id": card.id,
                        "message_id": context.message.message_id,
                        "back": back_state,
                    },
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                )
            )
            back_button = await token_button(
                session,
                context.owner_id,
                "↩️ Back",
                "card_view",
                {"id": card.id, "back": back_state},
            )
            await session.commit()
            await send_registered(
                context.message,
                context.services,
                "<b>Mark Card as blocked</b>\n\nDescribe what is blocking it.",
                kind=MessageKind.CARD_EDITOR,
                markup=InlineKeyboardMarkup(inline_keyboard=[[back_button]]),
            )
            return
        await update_card_fields(
            session,
            card.id,
            {field: not bool(getattr(card, field))},
        )
        await session.commit()
    await render_card(context.message, context.services, context.payload["id"])


async def _on_card_toggle_relation(context: CallbackContext) -> None:
    """Toggle one Category, Energy type, Value or Tag and reopen the same selector."""
    field = CARD_RELATION_TOGGLES[context.action]
    relation = RELATION_CHOICES[field]
    async with context.sessions() as session:
        await relation.toggle(
            session,
            context.payload["id"],
            relation.parse(context.payload[relation.payload_key]),
        )
        await session.commit()
    await render_card_choices(
        context.message, context.services, f"card_choose_{field}", context.payload["id"]
    )


async def _on_card_archive(context: CallbackContext) -> None:
    async with context.sessions() as session:
        count = len(await archive_subtree(session, context.payload["id"]))
        undo = await token_button(
            session,
            context.owner_id,
            "Undo archive",
            "card_restore",
            {"id": context.payload["id"]},
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Archived {count} card(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[[undo], menu_row()]),
    )


async def _on_card_restore(context: CallbackContext) -> None:
    async with context.sessions() as session:
        count = len(await archive_subtree(session, context.payload["id"], archive=False))
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Restored {count} card(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def _on_card_delete_prompt(context: CallbackContext) -> None:
    async with context.sessions() as session:
        confirm = await token_button(
            session,
            context.owner_id,
            "Permanently delete tree",
            "card_delete_confirm",
            {"id": context.payload["id"]},
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        "<b>Final confirmation</b>\nThis removes the tree and its historical contribution.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], menu_row()]),
    )


async def _on_card_delete_confirm(context: CallbackContext) -> None:
    async with context.sessions() as session:
        count = await delete_subtree(session, context.payload["id"])
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Permanently deleted {count} card(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def _on_card_finish(context: CallbackContext) -> None:
    card_id = int(context.payload["id"])
    stage = CardStage(context.payload["stage"])
    if stage is CardStage.DONE:
        async with context.sessions() as session:
            blocking = await pending_checks(session, card_id)
        if blocking:
            # Done is gated: the user answers each Check on its own screen, and nothing
            # is written until Save, so backing out leaves the Card live.
            await render_check_resolution(
                context.message,
                context.services,
                card_id,
                back={"kind": "card", "id": card_id},
            )
            return
    async with context.sessions() as session:
        result = await finish_action(session, card_id, stage)
        await session.commit()
    notice = "⚠️ " + "; ".join(result.warnings) if result.warnings else None
    if context.payload["stage"] == CardStage.DONE.value:
        await render_feedback(context.message, context.services, notice=notice)
        return
    await send_registered(
        context.message,
        context.services,
        with_notice("Card updated.", notice),
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


# --- Checks --------------------------------------------------------------------


def _check_back(context: CallbackContext) -> dict:
    return dict(context.payload.get("back") or {"kind": "home"})


async def _on_check_list(context: CallbackContext) -> None:
    await render_checks(
        context.message,
        context.services,
        dict(context.payload["scope"]),
        back=_check_back(context),
    )


async def _on_check_view(context: CallbackContext) -> None:
    await render_check(
        context.message,
        context.services,
        int(context.payload["id"]),
        scope=dict(context.payload["scope"]),
        back=_check_back(context),
    )


async def _on_check_edit_text(context: CallbackContext) -> None:
    await render_check_text_prompt(
        context.message,
        context.services,
        check_id=int(context.payload["id"]),
        field=str(context.payload["field"]),
        scope=dict(context.payload["scope"]),
        back=_check_back(context),
    )


async def _on_check_create_prompt(context: CallbackContext) -> None:
    await render_check_text_prompt(
        context.message,
        context.services,
        check_id=None,
        field="title",
        scope=dict(context.payload["scope"]),
        back=_check_back(context),
    )


async def _on_check_toggle_repeat(context: CallbackContext) -> None:
    async with context.sessions() as session:
        check = await session.get(Check, int(context.payload["id"]))
        if check is None:
            raise DomainError("Check does not exist")
        await update_check_fields(session, check.id, {"repeatable": not check.repeatable})
        await session.commit()
    await _on_check_view(context)


async def _on_check_cycle_status(context: CallbackContext) -> None:
    notice = None
    async with context.sessions() as session:
        check = await session.get(Check, int(context.payload["id"]))
        if check is None:
            raise DomainError("Check does not exist")
        _answered, successor = await resolve_check(session, check.id, next_outcome(check.outcome))
        await session.commit()
        if successor is not None:
            notice = "A new Pending Check was created for the next round."
    await render_check(
        context.message,
        context.services,
        int(context.payload["id"]),
        scope=dict(context.payload["scope"]),
        back=_check_back(context),
        notice=notice,
    )


async def _on_check_archive(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await archive_check(session, int(context.payload["id"]))
        await session.commit()
    await render_checks(
        context.message,
        context.services,
        dict(context.payload["scope"]),
        back=_check_back(context),
        notice="The Check was archived.",
    )


async def _on_check_resolve_cycle(context: CallbackContext) -> None:
    card_id = int(context.payload["card_id"])
    check_id = str(context.payload["check_id"])
    async with context.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        if editor is None or editor.kind != "check_resolve":
            raise DomainError("This Check screen expired")
        state = dict(editor.state)
    outcomes = dict(state.get("outcomes") or {})
    outcomes[check_id] = next_outcome(outcomes.get(check_id))
    await render_check_resolution(
        context.message,
        context.services,
        card_id,
        back=dict(state.get("back") or {"kind": "home"}),
        outcomes=outcomes,
    )


async def _on_check_resolve_save(context: CallbackContext) -> None:
    card_id = int(context.payload["card_id"])
    async with context.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        if editor is None or editor.kind != "check_resolve":
            raise DomainError("This Check screen expired")
        outcomes = {int(key): value for key, value in (editor.state.get("outcomes") or {}).items()}
        result = await finish_action(session, card_id, CardStage.DONE, check_outcomes=outcomes)
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
    notice = "⚠️ " + "; ".join(result.warnings) if result.warnings else None
    await render_feedback(context.message, context.services, notice=notice)


async def _on_check_resolve_cancel(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
    await render_card(
        context.message,
        context.services,
        int(context.payload["id"]),
        notice="The Card is still live; its Checks were not changed.",
    )


async def _on_proposal_check_cycle(context: CallbackContext) -> None:
    """Cycle one answer inside a Check-resolution proposal without approving it."""
    proposal_id = int(context.payload["id"])
    check_id = str(context.payload["check_id"])
    async with context.sessions() as session:
        change = await session.scalar(
            select(ProposalChange).where(ProposalChange.proposal_id == proposal_id)
        )
        if change is None or change.action != "resolve_for_card":
            raise DomainError("This proposal no longer accepts Check answers")
        values = dict(change.values)
        outcomes = dict(values.get("outcomes") or {})
        if check_id not in outcomes:
            raise DomainError("That Check is not part of this proposal")
        outcomes[check_id] = next_outcome(outcomes[check_id])
        values["outcomes"] = outcomes
        change.values = values
        await session.commit()
    await render_proposal(context.message, context.services, proposal_id)


async def _on_feedback(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await set_feedback(session, context.payload["id"], bool(context.payload["liked"]))
        await session.commit()
    await render_feedback(context.message, context.services)


# --- Sprint --------------------------------------------------------------------


async def _on_sprint_start(context: CallbackContext) -> None:
    async with context.sessions() as session:
        profile = await session.get(UserProfile, 1)
        sprint = await start_sprint(
            session, capacity=profile.capacity_effort_points if profile else None
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Sprint {sprint.number} started.",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def _on_sprint_finish(context: CallbackContext) -> None:
    async with context.sessions() as session:
        sprint = await finish_sprint(session, reason="finished_early")
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Sprint {sprint.number} finished early.",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


# --- AI proposals --------------------------------------------------------------


async def _on_proposal_approve(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        destructive = await session.scalar(
            select(ProposalChange.id).where(
                ProposalChange.proposal_id == proposal_id,
                ProposalChange.action == "delete",
            )
        )
        if destructive:
            confirm = await token_button(
                session,
                context.owner_id,
                "Permanently delete",
                "proposal_delete_confirm",
                {"id": proposal_id},
            )
            await session.commit()
            await send_registered(
                context.message,
                context.services,
                "<b>Final destructive confirmation</b>\nThis permanently removes the "
                "selected tree and its historical contribution.",
                kind=MessageKind.APPROVAL,
                markup=InlineKeyboardMarkup(inline_keyboard=[[confirm]]),
            )
            return
        affected = await ProposalService(session).apply(proposal_id)
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        "proposal",
        proposal_id,
        decision="approved",
        result={"affected_ids": affected},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        f"✅ Saved proposal. Updated {len(affected)} item(s).",
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


async def _on_proposal_delete_confirm(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        affected = await ProposalService(session).apply(proposal_id, allow_destructive=True)
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        "proposal",
        proposal_id,
        decision="approved",
        result={"affected_ids": affected, "destructive": True},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        f"Permanently deleted {len(affected)} selected item(s).",
        kind=MessageKind.RECEIPT,
    )


async def _on_proposal_reject(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        await ProposalService(session).reject(proposal_id)
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        "proposal",
        proposal_id,
        decision="discarded",
        result={"message": "The user discarded this proposed change."},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        "🗑 Proposal discarded.",
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "value_create_prompt": _on_item_create_prompt,
    "tag_create_prompt": _on_item_create_prompt,
    "item_view": _on_item_view,
    "item_edit_text": _on_item_edit_text,
    "item_text_back": _on_item_text_back,
    "item_create": _on_item_create,
    "item_toggle_focus": _on_item_toggle_focus,
    "item_archive_prompt": _on_item_archive_prompt,
    "item_archive_confirm": _on_item_archive_confirm,
    "item_back": _on_item_back,
    "request_view": _on_request_view,
    "subsession_confirm": _on_subsession_confirm,
    "subsession_cancel": _on_subsession_cancel,
    "card_create_view": _on_card_draft_view,
    "card_create_edit_text": _on_card_draft_edit_text,
    "card_create_toggle": _on_card_draft_toggle,
    "card_create_set": _on_card_draft_set,
    "card_create_save": _on_card_draft_save,
    "card_create_discard": _on_card_draft_discard,
    "dashboard_page": _on_dashboard_page,
    "card_view": _on_card_view,
    "card_children": _on_card_children,
    "card_back": _on_card_back,
    "card_move": _on_card_move,
    "card_edit_text": _on_card_edit_text,
    "card_set_field": _on_card_set_field,
    "card_toggle_field": _on_card_toggle_field,
    "card_archive": _on_card_archive,
    "card_restore": _on_card_restore,
    "card_delete_prompt": _on_card_delete_prompt,
    "card_delete_confirm": _on_card_delete_confirm,
    "card_finish": _on_card_finish,
    "card_checks": _on_check_list,
    "item_checks": _on_check_list,
    "check_view": _on_check_view,
    "check_edit_text": _on_check_edit_text,
    "check_create_prompt": _on_check_create_prompt,
    "check_toggle_repeat": _on_check_toggle_repeat,
    "check_cycle_status": _on_check_cycle_status,
    "check_archive": _on_check_archive,
    "check_list_back": _on_check_list,
    "check_back": _on_card_back,
    "check_resolve_cycle": _on_check_resolve_cycle,
    "check_resolve_save": _on_check_resolve_save,
    "check_resolve_cancel": _on_check_resolve_cancel,
    "proposal_check_cycle": _on_proposal_check_cycle,
    "feedback": _on_feedback,
    "sprint_start": _on_sprint_start,
    "sprint_finish": _on_sprint_finish,
    "proposal_approve": _on_proposal_approve,
    "proposal_delete_confirm": _on_proposal_delete_confirm,
    "proposal_reject": _on_proposal_reject,
    **{f"card_choose_{field}": _on_card_choices for field in CARD_CHOICE_FIELDS},
    **{
        f"card_create_choose_{field}": _on_card_draft_chooser
        for field in CARD_DRAFT_CHOICE_FIELDS
    },
    **dict.fromkeys(CARD_DRAFT_RELATIONS, _on_card_draft_toggle_relation),
    **dict.fromkeys(CARD_RELATION_TOGGLES, _on_card_toggle_relation),
}


async def _report_callback_failure(
    context: CallbackContext, *, notice: str, fallback: str
) -> None:
    """Keep a still-pending proposal reviewable, otherwise state what failed.

    A failed approval leaves the proposal pending, so the owner needs its screen back
    rather than a bare error above a dead message.
    """
    if context.action.startswith("proposal_") and context.payload.get("id"):
        try:
            await render_proposal(
                context.message,
                context.services,
                context.payload["id"],
                notice=notice,
            )
            return
        except DomainError:
            pass
        except Exception:
            logger.exception("Could not restore proposal UI after callback failure")
    await send_registered(context.message, context.services, fallback, kind=MessageKind.ERROR)


async def _resume_failed_approval(context: CallbackContext, error: Exception) -> bool:
    """Let a suspended agent turn observe an approval that could not be applied."""
    if context.action not in {"proposal_approve", "proposal_delete_confirm"}:
        return False
    return await continue_agent_approval(
        context.message,
        context.services,
        "proposal",
        context.payload["id"],
        decision="failed",
        result={"error": str(error)},
    )


@router.callback_query(F.data.startswith("cb:"))
async def callback_token_handler(callback: CallbackQuery, services: Services) -> None:
    if not callback.message:
        return
    token_value = callback.data.split(":", 1)[1]
    async with services.sessions() as session:
        now = datetime.now(UTC)
        claimed = (
            await session.execute(
                update(CallbackToken)
                .where(
                    CallbackToken.token == token_value,
                    CallbackToken.owner_id == services.owner_id,
                    CallbackToken.expires_at >= now,
                    CallbackToken.consumed_at.is_(None),
                )
                .values(consumed_at=now)
                .returning(CallbackToken.action, CallbackToken.payload)
            )
        ).one_or_none()
        if claimed is None:
            await callback.answer("This action expired. Reopen the screen.", show_alert=True)
            return
        action, payload = claimed
        await session.commit()
    await callback.answer()

    context = CallbackContext(callback, services, action, dict(payload or {}))
    handler = CALLBACK_ACTIONS.get(action)
    if handler is None:
        logger.warning("Unknown Telegram callback action: %s", action)
        await send_registered(
            context.message,
            services,
            "This action is no longer available. Reopen the screen.",
            kind=MessageKind.ERROR,
        )
        return

    try:
        await handler(context)
    except StaleStateError as error:
        if action.startswith("proposal_") and payload.get("id"):
            async with services.sessions() as session:
                proposal = await session.get(ChangeProposal, payload["id"])
                if proposal:
                    proposal.status = ProposalStatus.STALE.value
                    await session.commit()
        if await _resume_failed_approval(context, error):
            return
        await send_registered(
            context.message, services, html.escape(str(error)), kind=MessageKind.ERROR
        )
    except DomainError as error:
        if await _resume_failed_approval(context, error):
            return
        await _report_callback_failure(
            context,
            notice=f"⚠️ {error}",
            fallback=html.escape(str(error)),
        )
    except Exception:
        logger.exception("Telegram callback failed: action=%s payload=%s", action, payload)
        await _report_callback_failure(
            context,
            notice=(
                "⚠️ This action failed. The proposal is still pending; "
                "you can retry or discard it."
            ),
            fallback="Safwa could not finish this action. Reopen the screen and try again.",
        )
