from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from typing import Any

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import (
    DomainError,
    StaleStateError,
    archive_subtree,
    create_card,
    create_tag,
    create_value,
    delete_check,
    delete_subtree,
    delete_tag,
    delete_value,
    finish_action,
    finish_sprint,
    move_card,
    resolve_check,
    set_value_focus,
    start_sprint,
    unobserved_series,
    update_card_fields,
    update_check_fields,
)
from ..enums import MessageKind
from ..features.cards.model import CardStage
from ..features.checks.model import CheckOutcome
from ..features.checks.use_cases import toggle_check_value
from ..features.proposals.model import BatchDecision
from ..features.proposals.use_cases import approve_proposal
from ..features.reminders.use_cases import delete_reminder
from ..models import (
    CallbackToken,
    Card,
    Check,
    UiSession,
    Workspace,
)
from ._core import (
    CARD_CHOICE_FIELDS,
    CARD_DRAFT_CHOICE_FIELDS,
    CARD_DRAFT_RELATIONS,
    CARD_RELATION_TOGGLES,
    ITEM_CARRIERS,
    RELATION_CHOICES,
    CallbackContext,
    CallbackHandler,
    Services,
    router,
)
from ._messaging import send_registered, token_button
from ._presentation import menu_row, proposal_outcome_text, with_notice
from .cards import (
    card_creation_errors,
    card_editor_back_state,
    carrier_counts,
    carrier_phrase,
    handle_card_creation_chooser,
    render_card,
    render_card_choices,
    render_card_creation,
    render_children,
    render_dashboard,
    require_card_draft,
    sanitize_card_creation_state,
)
from .checks import (
    render_check,
    render_check_resolution,
    render_check_values,
    render_checks,
)
from .commands import (
    command_start,
    command_tags,
    command_values,
    sync_bot_commands,
)
from .items import (
    render_item_editor,
    render_item_text_prompt,
    render_saved_request,
)
from .plan import (
    PLAN_UI_KIND,
    on_plan_card,
    on_plan_filter_toggle,
    on_plan_filters,
    on_plan_move,
    on_plan_open,
    on_plan_page,
    render_plan,
)
from .proposals import continue_agent_approval, render_proposal
from .reminders import (
    render_reminder,
    render_reminder_delete_prompt,
    render_reminder_text_prompt,
    render_reminders,
)
from .sprint import render_sprint, render_sprint_criteria_prompt, render_today
from .text_input import TextInputScreen, render_text_input

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
        # A description box the owner never typed into is not a description they gave.
        item = await create(
            session, values.get("name", ""), values.get("description", "").strip() or None
        )
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


async def _on_item_delete_prompt(context: CallbackContext) -> None:
    entity = context.payload["entity"]
    carriers = ITEM_CARRIERS[entity]
    async with context.sessions() as session:
        item = await session.get(carriers[0].model, context.payload["id"])
        if item is None:
            raise DomainError(f"{entity.title()} does not exist")
        carried_by = await carrier_counts(session, carriers, item.id)
        confirm = await token_button(
            session,
            context.owner_id,
            f"Delete {entity.title()}",
            "item_delete_confirm",
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
        f"<b>Delete {html.escape(entity.title())}?</b>\n"
        f"{html.escape(item.name)} will be deleted and taken off "
        f"{carrier_phrase(carried_by)}. Nothing it is on is deleted.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [back]]),
        related_id=item.id,
    )


async def _on_item_delete_confirm(context: CallbackContext) -> None:
    entity = context.payload["entity"]
    async with context.sessions() as session:
        remove = delete_tag if entity == "tag" else delete_value
        item, removed = await remove(session, context.payload["id"])
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
        f"Deleted <b>{html.escape(item.name)}</b>. Taken off {removed} link(s).",
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
        state.pop("text_input", None)
        state.pop("flow", None)
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


async def _render_dashboard_state(
    context: CallbackContext, state: dict[str, Any], *, notice: str | None = None
) -> None:
    """Return to the Card list a screen came from, whichever of the four it is."""
    page = int(state.get("page", 0))
    mode = state.get("mode")
    if mode == "today":
        await render_today(context.message, context.services, page=page, notice=notice)
    elif mode == "sprint":
        await render_sprint(context.message, context.services, page=page, notice=notice)
    else:
        await render_dashboard(
            context.message,
            context.services,
            CardStage(state["stage"]),
            title=state.get("title", "Backlog"),
            page=page,
            notice=notice,
        )


async def _on_dashboard_page(context: CallbackContext) -> None:
    await _render_dashboard_state(context, context.payload)


async def _on_card_quick_move(context: CallbackContext) -> None:
    """One tap moves an Action between Sprint and Today, then shows the same list again."""
    async with context.sessions() as session:
        result = await move_card(
            session, int(context.payload["id"]), CardStage(context.payload["stage"])
        )
        await session.commit()
    notice = "⚠️ " + "; ".join(result.warnings) if result.warnings else None
    await _render_dashboard_state(
        context, dict(context.payload.get("back") or {}), notice=notice
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
        await _render_dashboard_state(context, back)
    elif back["kind"] == "request":
        await render_saved_request(context.message, context.services, int(back["id"]))
    elif back["kind"] == PLAN_UI_KIND:
        await render_plan(
            context.message,
            context.services,
            page=int(back.get("page", 0)),
            filters=list(back.get("filters", [])),
        )
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
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        current = str(getattr(card, field) or "")
    await render_text_input(
        context.message,
        context.services,
        screen=TextInputScreen(
            title=f"Edit Card {field.replace('_', ' ').title()}",
            current_value=current,
            instruction=f"Send the new {field.replace('_', ' ')}.",
            back_action="card_view",
            back_payload={"id": card_id, "back": back_state},
            related_id=card_id,
        ),
        state={"flow": "card", "card_id": card_id, "field": field, "back": back_state},
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
    blocked_prompt: tuple[int, str, dict[str, Any]] | None = None
    async with context.sessions() as session:
        card = await session.get(Card, context.payload["id"])
        if card is None:
            raise DomainError("Card does not exist")
        if field == "blocked" and not card.blocked:
            # Blocking always needs a reason, so ask for it before writing anything.
            back_state = await card_editor_back_state(session, context.owner_id)
            blocked_prompt = (card.id, str(card.blocked_description or ""), back_state)
        else:
            await update_card_fields(
                session,
                card.id,
                {field: not bool(getattr(card, field))},
            )
            await session.commit()
    if blocked_prompt is not None:
        card_id, current, back_state = blocked_prompt
        await render_text_input(
            context.message,
            context.services,
            screen=TextInputScreen(
                title="Mark Card as blocked",
                current_value=current,
                instruction="Describe what is blocking it.",
                back_action="card_view",
                back_payload={"id": card_id, "back": back_state},
                related_id=card_id,
            ),
            state={"flow": "card_blocked", "card_id": card_id, "back": back_state},
        )
        return
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
        context.message,
        context.services,
        f"card_choose_{field}",
        context.payload["id"],
        page=int(context.payload.get("page", 0)),
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
            blocking = await unobserved_series(session, card_id)
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


def _check_card_id(context: CallbackContext) -> int | None:
    """A Check opened by the advisor, or hanging on no Card, has no owning Card screen."""
    card_id = context.payload.get("card_id")
    return int(card_id) if card_id is not None else None


async def _on_check_list(context: CallbackContext) -> None:
    await render_checks(
        context.message,
        context.services,
        int(context.payload["card_id"]),
        back=_check_back(context),
    )


async def _on_check_choose_values(context: CallbackContext) -> None:
    await render_check_values(
        context.message,
        context.services,
        context.payload["id"],
        card_id=context.payload.get("card_id"),
        back=context.payload.get("back"),
        page=int(context.payload.get("page", 0)),
    )


async def _on_check_toggle_value(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await toggle_check_value(
            session, context.payload["id"], context.payload["value_id"]
        )
        await session.commit()
    await _on_check_choose_values(context)


async def _on_check_view(context: CallbackContext) -> None:
    await render_check(
        context.message,
        context.services,
        int(context.payload["id"]),
        card_id=_check_card_id(context),
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


async def _on_check_delete_prompt(context: CallbackContext) -> None:
    async with context.sessions() as session:
        check = await session.get(Check, int(context.payload["id"]))
        if check is None:
            raise DomainError("Check does not exist")
        confirm = await token_button(
            session,
            context.owner_id,
            "Permanently delete Check",
            "check_delete_confirm",
            context.payload,
        )
        back = await token_button(
            session, context.owner_id, "↩️ Back", "check_view", context.payload
        )
        title = check.title
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Delete Check?</b>\n{html.escape(title)} will be deleted, answered or not. "
        f"Its Card stays, and so do the Values it pointed at.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [back]]),
    )


async def _on_check_delete_confirm(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await delete_check(session, int(context.payload["id"]))
        await session.commit()
    # Back to whatever list the Check was opened from: the Check itself is gone.
    await _on_check_list(context)


async def _on_check_set_status(context: CallbackContext) -> None:
    notice = None
    async with context.sessions() as session:
        check = await session.get(Check, int(context.payload["id"]))
        if check is None:
            raise DomainError("Check does not exist")
        _answered, successor = await resolve_check(
            session, check.id, CheckOutcome(str(context.payload["outcome"]))
        )
        await session.commit()
        if successor is not None:
            notice = "A new Pending Check was created for the next round."
    await render_check(
        context.message,
        context.services,
        int(context.payload["id"]),
        card_id=_check_card_id(context),
        back=_check_back(context),
        notice=notice,
    )


async def _on_check_resolve_set(context: CallbackContext) -> None:
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
    outcomes[check_id] = CheckOutcome(str(context.payload["outcome"])).value
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
        state = dict(editor.state)
        stored = dict(state.get("outcomes") or {})
    if any(value is None for value in stored.values()):
        await render_check_resolution(
            context.message,
            context.services,
            card_id,
            back=dict(state.get("back") or {"kind": "home"}),
            outcomes=stored,
            notice="Answer every Check before saving.",
        )
        return
    async with context.sessions() as session:
        outcomes = {int(key): value for key, value in stored.items()}
        result = await finish_action(session, card_id, CardStage.DONE, check_outcomes=outcomes)
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
    notice = "⚠️ " + "; ".join(result.warnings) if result.warnings else None
    await send_registered(
        context.message,
        context.services,
        with_notice("Card updated.", notice),
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


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


# --- Sprint --------------------------------------------------------------------


async def _on_sprint_criteria_prompt(context: CallbackContext) -> None:
    await render_sprint_criteria_prompt(context.message, context.services)


async def _on_sprint_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
    await render_sprint(context.message, context.services)


async def _on_sprint_start(context: CallbackContext) -> None:
    async with context.sessions() as session:
        workspace = await session.get(Workspace, 1)
        sprint = await start_sprint(
            session,
            success_criteria=workspace.sprint_success_criteria if workspace else "",
        )
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
        number, end_date = sprint.number, sprint.planned_end_date
    await sync_bot_commands(
        context.message.bot, context.services.commands, sprint_active=True
    )
    await render_sprint(
        context.message,
        context.services,
        notice=f"Sprint {number} started. It ends on {end_date}.",
    )


async def _on_sprint_finish(context: CallbackContext) -> None:
    async with context.sessions() as session:
        sprint = await finish_sprint(session, reason="finished_early")
        await session.commit()
        number = sprint.number
    await sync_bot_commands(
        context.message.bot, context.services.commands, sprint_active=False
    )
    await render_sprint(
        context.message, context.services, notice=f"Sprint {number} finished early."
    )


# --- Settings ------------------------------------------------------------------


async def _on_settings_edit(context: CallbackContext) -> None:
    # Imported here, not above: the Settings screen lives in its feature and reaches back
    # into this package, so a module-level import makes `features.profile.screens` the
    # start of a cycle whenever it is the first thing imported. The import moves with the
    # handlers in Phase 8.b.
    from ..features.profile.screens import render_settings_field_prompt

    await render_settings_field_prompt(
        context.message, context.services, str(context.payload["field"])
    )


async def _on_settings_back(context: CallbackContext) -> None:
    from ..features.profile.screens import command_settings

    async with context.sessions() as session:
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
    await command_settings(context.message, context.services)


# --- AI proposals --------------------------------------------------------------


async def _on_reminders_page(context: CallbackContext) -> None:
    await render_reminders(context.message, context.services, page=int(context.payload.get("page", 0)))


async def _on_reminder_view(context: CallbackContext) -> None:
    await render_reminder(context.message, context.services, int(context.payload["id"]))


async def _on_reminder_text_prompt(context: CallbackContext) -> None:
    await render_reminder_text_prompt(
        context.message, context.services, int(context.payload["id"])
    )


async def _on_reminder_delete_prompt(context: CallbackContext) -> None:
    await render_reminder_delete_prompt(
        context.message, context.services, int(context.payload["id"])
    )


async def _on_reminder_delete_confirm(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await delete_reminder(session, int(context.payload["id"]))
        await session.commit()
    await render_reminders(context.message, context.services)


async def _on_proposal_approve(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        # Which changes need a second confirmation is the owning feature's rule, not this
        # screen's: a Card deletion takes a whole subtree and its historical contribution.
        advisor = context.services.advisor
        review = advisor.reviews.proposal(proposal_id)
        if review is not None and any(
            advisor.proposals.needs_confirmation(change) for change in review.changes
        ):
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
        # Read the description before applying: its field lines diff against committed
        # state, which the apply is about to become.
        description = await context.services.advisor.describe_proposal(session, proposal_id)
        affected = await approve_proposal(
            session, advisor.reviews, advisor.proposals, proposal_id
        )
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        proposal_outcome_text(BatchDecision.APPROVED, description.summary, description.fields),
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


async def _on_proposal_delete_confirm(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        description = await context.services.advisor.describe_proposal(session, proposal_id)
        affected = await approve_proposal(
            session,
            context.services.advisor.reviews,
            context.services.advisor.proposals,
            proposal_id,
            allow_destructive=True,
        )
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected, "destructive": True},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        proposal_outcome_text(
            BatchDecision.APPROVED,
            description.summary,
            description.fields,
            notice=f"Permanently deleted {len(affected)} item(s).",
        ),
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


async def _on_proposal_reject(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        description = await context.services.advisor.describe_proposal(session, proposal_id)
    context.services.advisor.reviews.end_proposal(proposal_id)
    if await continue_agent_approval(
        context.message,
        context.services,
        proposal_id,
        decision=BatchDecision.DISCARDED,
        result={"message": "The user discarded this proposed change."},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        proposal_outcome_text(BatchDecision.DISCARDED, description.summary, description.fields),
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "value_create_prompt": _on_item_create_prompt,
    "tag_create_prompt": _on_item_create_prompt,
    "item_view": _on_item_view,
    "item_edit_text": _on_item_edit_text,
    "item_text_back": _on_item_text_back,
    "check_choose_values": _on_check_choose_values,
    "check_toggle_value": _on_check_toggle_value,
    "item_create": _on_item_create,
    "item_toggle_focus": _on_item_toggle_focus,
    "item_delete_prompt": _on_item_delete_prompt,
    "item_delete_confirm": _on_item_delete_confirm,
    "item_back": _on_item_back,
    "request_view": _on_request_view,
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
    "check_view": _on_check_view,
    "check_toggle_repeat": _on_check_toggle_repeat,
    "check_set_status": _on_check_set_status,
    "check_delete_prompt": _on_check_delete_prompt,
    "check_delete_confirm": _on_check_delete_confirm,
    "check_list_back": _on_check_list,
    "check_back": _on_card_back,
    "check_resolve_set": _on_check_resolve_set,
    "check_resolve_save": _on_check_resolve_save,
    "check_resolve_cancel": _on_check_resolve_cancel,
    "card_quick_move": _on_card_quick_move,
    "sprint_criteria_prompt": _on_sprint_criteria_prompt,
    "sprint_back": _on_sprint_back,
    "sprint_start": _on_sprint_start,
    "sprint_finish": _on_sprint_finish,
    "plan_open": on_plan_open,
    "plan_page": on_plan_page,
    "plan_move": on_plan_move,
    "plan_card": on_plan_card,
    "plan_filters": on_plan_filters,
    "plan_filter_toggle": on_plan_filter_toggle,
    "settings_edit": _on_settings_edit,
    "settings_back": _on_settings_back,
    "reminders_page": _on_reminders_page,
    "reminder_view": _on_reminder_view,
    "reminder_text_prompt": _on_reminder_text_prompt,
    "reminder_delete_prompt": _on_reminder_delete_prompt,
    "reminder_delete_confirm": _on_reminder_delete_confirm,
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

PROPOSAL_APPLY_CALLBACK_ACTIONS = frozenset({"proposal_approve", "proposal_delete_confirm"})


async def _report_callback_failure(
    context: CallbackContext, *, notice: str, fallback: str
) -> None:
    """Restore a still-pending screen when possible, otherwise state what failed."""
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
    if context.action not in PROPOSAL_APPLY_CALLBACK_ACTIONS:
        return False
    return await continue_agent_approval(
        context.message,
        context.services,
        context.payload["id"],
        decision=BatchDecision.FAILED,
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
                    CallbackToken.consumed_at.is_(None),
                )
                .values(consumed_at=now)
                .returning(CallbackToken.action, CallbackToken.payload)
            )
        ).one_or_none()
        if claimed is None:
            # A token that is still here was pressed a second time; one that is gone
            # belongs to a screen drawn by a run of Safwa that has ended.
            pressed_again = await session.scalar(
                select(CallbackToken.token).where(CallbackToken.token == token_value)
            )
            await session.commit()
            if pressed_again is not None:
                await callback.answer("This action expired. Reopen the screen.", show_alert=True)
                return
            await callback.answer()
            await send_registered(
                callback.message,
                services,
                "This screen is out of date. Reopen it from the menu.",
                kind=MessageKind.ERROR,
            )
            return
        action, payload = claimed
        await session.commit()

    context = CallbackContext(callback, services, action, dict(payload or {}))
    await callback.answer()
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
        # The refusal already ended the review, so the screen is unanswerable by then.
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
