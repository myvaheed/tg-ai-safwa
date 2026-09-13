"""What a tap on a Card or on a Card list does, and every button Cards publishes."""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    TextInputScreen,
    go_back,
    go_back_action,
    menu_row,
    render_text_input,
    send_registered,
    token_button,
    with_notice,
)

from ...checks.use_cases import unobserved_series
from ..hard_time import HARD_TIME_INSTRUCTION, hard_time_text, workspace_zone
from ..model import Card, CardStage
from ..use_cases import (
    archive_subtree,
    delete_one_card,
    delete_subtree,
    finish_action,
    move_card,
    update_card_fields,
)
from .creation import CARD_DRAFT_ACTIONS
from .done_gate import CARD_DONE_ACTIONS, render_check_resolution
from .draft import card_editor_back_state
from .lists import render_children, render_dashboard
from .screens import render_card
from .selectors import (
    CARD_CHOICE_FIELDS,
    CARD_RELATION_TOGGLES,
    RELATION_CHOICES,
    render_card_choices,
)


async def _on_dashboard_page(context: CallbackContext) -> None:
    await render_dashboard(
        context.message,
        context.services,
        CardStage(context.payload["stage"]),
        title=context.payload.get("title", "Backlog"),
        page=int(context.payload.get("page", 0)),
        notice=context.payload.get("notice"),
    )


async def _on_quick_move(context: CallbackContext) -> None:
    """One tap moves an Action between Sprint and Today, then shows the same list again."""
    async with context.sessions() as session:
        result = await move_card(
            session, int(context.payload["id"]), CardStage(context.payload["stage"])
        )
        await session.commit()
    notice = "⚠️ " + "; ".join(result.warnings) if result.warnings else None
    await go_back(context, context.payload.get("back"), notice=notice)


async def _on_view(context: CallbackContext) -> None:
    await render_card(
        context.message,
        context.services,
        context.payload["id"],
        back=context.payload.get("back"),
        full=context.payload.get("full"),
        notice=context.payload.get("notice"),
    )


async def _on_view_mode(context: CallbackContext) -> None:
    """Swap the same Card between the compact view and full editing."""
    await render_card(
        context.message,
        context.services,
        context.payload["id"],
        full=bool(context.payload["full"]),
    )


async def _on_children(context: CallbackContext) -> None:
    await render_children(
        context.message,
        context.services,
        context.payload["id"],
        page=int(context.payload.get("page", 0)),
        back=context.payload.get("back"),
    )


async def _on_move(context: CallbackContext) -> None:
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


async def _on_edit_text(context: CallbackContext) -> None:
    card_id = context.payload["id"]
    field = context.payload["field"]
    async with context.sessions() as session:
        back_state = await card_editor_back_state(session, context.owner_id)
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        if field == "hard_time":
            current = hard_time_text(card.hard_time, tz=await workspace_zone(session)) or ""
        else:
            current = str(getattr(card, field) or "")
    await render_text_input(
        context.message,
        context.services,
        screen=TextInputScreen(
            title=f"Edit Card {field.replace('_', ' ').title()}",
            current_value=current,
            instruction=(
                HARD_TIME_INSTRUCTION
                if field == "hard_time"
                else f"Send the new {field.replace('_', ' ')}."
            ),
            back_action="card_view",
            back_payload={"id": card_id, "back": back_state, "full": True},
            related_id=card_id,
        ),
        state={"flow": "card", "card_id": card_id, "field": field, "back": back_state},
    )


async def _on_choices(context: CallbackContext) -> None:
    await render_card_choices(
        context.message,
        context.services,
        context.action,
        context.payload["id"],
        page=int(context.payload.get("page", 0)),
    )


async def _on_set_field(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await update_card_fields(
            session,
            context.payload["id"],
            {context.payload["field"]: context.payload["value"]},
        )
        await session.commit()
    await render_card(context.message, context.services, context.payload["id"])


async def _on_toggle_field(context: CallbackContext) -> None:
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
                back_payload={"id": card_id, "back": back_state, "full": True},
                related_id=card_id,
            ),
            state={"flow": "card_blocked", "card_id": card_id, "back": back_state},
        )
        return
    await render_card(context.message, context.services, context.payload["id"])


async def _on_toggle_relation(context: CallbackContext) -> None:
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


async def _on_archive(context: CallbackContext) -> None:
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


async def _on_restore(context: CallbackContext) -> None:
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


async def _on_delete_prompt(context: CallbackContext) -> None:
    card_id = int(context.payload["id"])
    async with context.sessions() as session:
        children = await session.scalar(
            select(func.count()).select_from(Card).where(Card.parent_id == card_id)
        )
        rows: list[list[InlineKeyboardButton]] = []
        if children:
            rows.append(
                [
                    await token_button(
                        session,
                        context.owner_id,
                        "🗑 Delete this Card only",
                        "card_delete_confirm",
                        {"id": card_id, "subtree": False},
                    )
                ]
            )
        rows.append(
            [
                await token_button(
                    session,
                    context.owner_id,
                    "🗑 Permanently delete tree" if children else "🗑 Permanently delete",
                    "card_delete_confirm",
                    {"id": card_id, "subtree": True},
                )
            ]
        )
        await session.commit()
    text = "<b>Final confirmation</b>\nThis removes the tree and its historical contribution."
    if children:
        text += (
            "\nDeleting this Card alone keeps what is under it: a Subgoal becomes a Goal, "
            "and an Action is left under no one."
        )
    await send_registered(
        context.message,
        context.services,
        text,
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


async def _on_delete_confirm(context: CallbackContext) -> None:
    # Safwa only ever proposes the branch, so a payload with no answer means the branch.
    delete = delete_subtree if context.payload.get("subtree", True) else delete_one_card
    async with context.sessions() as session:
        count = await delete(session, context.payload["id"])
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Permanently deleted {count} card(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def _on_finish(context: CallbackContext) -> None:
    card_id = int(context.payload["id"])
    async with context.sessions() as session:
        blocking = await unobserved_series(session, card_id)
    if blocking:
        # Done is gated: the user answers each Check on its own screen, and nothing
        # is written until Save, so backing out leaves the Card live.
        await render_check_resolution(
            context.message,
            context.services,
            card_id,
            back={"action": "card_view", "id": card_id},
        )
        return
    async with context.sessions() as session:
        result = await finish_action(session, card_id)
        await session.commit()
    notice = "⚠️ " + "; ".join(result.warnings) if result.warnings else None
    await send_registered(
        context.message,
        context.services,
        with_notice("Card updated.", notice),
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


CARD_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "dashboard_page": _on_dashboard_page,
    "card_view": _on_view,
    "card_children": _on_children,
    "card_back": go_back_action,
    "card_move": _on_move,
    "card_edit_text": _on_edit_text,
    "card_set_field": _on_set_field,
    "card_toggle_field": _on_toggle_field,
    "card_archive": _on_archive,
    "card_restore": _on_restore,
    "card_view_mode": _on_view_mode,
    "card_delete_prompt": _on_delete_prompt,
    "card_delete_confirm": _on_delete_confirm,
    "card_finish": _on_finish,
    "card_quick_move": _on_quick_move,
    **{f"card_choose_{field}": _on_choices for field in CARD_CHOICE_FIELDS},
    **dict.fromkeys(CARD_RELATION_TOGGLES, _on_toggle_relation),
    **CARD_DRAFT_ACTIONS,
    **CARD_DONE_ACTIONS,
}
