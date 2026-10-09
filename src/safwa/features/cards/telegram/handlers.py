"""What a tap on a Card or on a Card list does, and every button Cards publishes."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    TextInputScreen,
    back_button,
    menu_row,
    render_text_input,
    send_registered,
    token_button,
    with_notice,
)

from ...checks.use_cases import pending_checks
from ...profile.api import effort_tracking_on
from ...schedules.api import DEADLINE_INSTRUCTION, SCHEDULE_INSTRUCTION
from ...schedules.telegram import render_schedule
from ..model import Card, CardKind, CardStage, minutes_label
from ..use_cases import (
    archive_subtree,
    delete_one_card,
    delete_subtree,
    finish_card,
    move_card,
    reopen_card,
    update_card_fields,
)
from .board import BOARD_ACTIONS
from .creation import CARD_DRAFT_ACTIONS
from .done_gate import CARD_DONE_ACTIONS, render_check_resolution
from .lists import render_children
from .screens import render_card
from .selectors import (
    CARD_CHOICE_FIELDS,
    CARD_RELATION_TOGGLES,
    RELATION_CHOICES,
    render_card_choices,
)
from .text_input import TIME_SPENT_INSTRUCTION


async def _on_view(context: CallbackContext) -> None:
    """The Card, or the same Card swapped between the compact view and full editing."""
    await render_card(
        context.message,
        context.services,
        context.payload["id"],
        back=context.back,
        full=context.payload.get("full"),
        notice=context.payload.get("notice"),
    )


async def _on_children(context: CallbackContext) -> None:
    await render_children(
        context.message,
        context.services,
        context.payload["id"],
        page=int(context.payload.get("page", 0)),
        back=context.back,
    )


async def _on_move(context: CallbackContext) -> None:
    async with context.sessions() as session:
        operation = reopen_card if context.payload.get("reopen") else move_card
        result = await operation(
            session, context.payload["id"], CardStage(context.payload["stage"])
        )
        await session.commit()
    await render_card(
        context.message,
        context.services,
        context.payload["id"],
        back=context.back,
        notice="⚠️ " + "; ".join(result.warnings) if result.warnings else None,
    )


_FIELD_TITLES = {"tracked_mins": "Time Spent"}
_FIELD_INSTRUCTIONS = {
    "schedule": SCHEDULE_INSTRUCTION,
    "tracked_mins": TIME_SPENT_INSTRUCTION,
}


def _full_card(context: CallbackContext, card_id: int) -> Place:
    """The full Card an editor opened from it stands in for: only that view has fields."""
    return Place("card_view", {"id": card_id, "full": True}, context.back)



async def _on_edit_text(context: CallbackContext) -> None:
    card_id = context.payload["id"]
    field = context.payload["field"]
    async with context.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        if field == "tracked_mins":
            current = minutes_label(card.tracked_mins) if card.tracked_mins else ""
        else:
            current = str(getattr(card, field) or "")
        deadline = field == "schedule" and card.kind != CardKind.ACTION.value
    title = "Deadline" if deadline else _FIELD_TITLES.get(field, field.replace("_", " ").title())
    await render_text_input(
        context.message,
        context.services,
        screen=TextInputScreen(
            title=f"Edit Card {title}",
            current_value=current,
            instruction=DEADLINE_INSTRUCTION
            if deadline
            else _FIELD_INSTRUCTIONS.get(field, f"Send the new {field.replace('_', ' ')}."),
            back=_full_card(context, card_id),
            related_id=card_id,
        ),
        state={"flow": "card", "card_id": card_id, "field": field},
    )


async def _on_open_schedule(context: CallbackContext) -> None:
    """A Schedule or Deadline with a clock still ahead offers Remind beside Edit; any other
    opens its editor at once."""
    card_id = context.payload["id"]
    shown = await render_schedule(
        context.message,
        context.services,
        Card,
        card_id,
        edit=Place("card_edit_text", {"id": card_id, "field": "schedule"}, context.back),
        back=_full_card(context, card_id),
    )
    if not shown:
        await _on_edit_text(context)


async def _on_choices(context: CallbackContext) -> None:
    await render_card_choices(
        context.message,
        context.services,
        context.action,
        context.payload["id"],
        page=int(context.payload.get("page", 0)),
        back=context.back,
    )


async def _on_set_field(context: CallbackContext) -> None:
    async with context.sessions() as session:
        if context.payload["field"] == "effort_points" and not await effort_tracking_on(session):
            raise DomainError("Effort Points are off. Turn them on in the Profile to estimate load.")
        await update_card_fields(
            session,
            context.payload["id"],
            {context.payload["field"]: context.payload["value"]},
        )
        await session.commit()
    await render_card(context.message, context.services, context.payload["id"], back=context.back)


async def _on_toggle_field(context: CallbackContext) -> None:
    """🚧 Blocked: a blocked Action is unblocked, and any other is asked its reason first,
    because the reason is what blocks it."""
    card_id = int(context.payload["id"])
    async with context.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        blocked = card.blocked
        if blocked:
            await update_card_fields(session, card.id, {"blocked_description": ""})
            await session.commit()
    if not blocked:
        await render_text_input(
            context.message,
            context.services,
            screen=TextInputScreen(
                title="Mark Card as blocked",
                current_value="",
                instruction="Describe what is blocking it.",
                back=_full_card(context, card_id),
                related_id=card_id,
            ),
            state={"flow": "card_blocked", "card_id": card_id},
        )
        return
    await render_card(context.message, context.services, card_id, back=context.back)


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
        back=context.back,
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
        rows.append(
            [
                await back_button(
                    session, context.owner_id, Place("card_view", {"id": card_id}, context.back)
                )
            ]
        )
        await session.commit()
    text = "<b>Final confirmation</b>\nThis removes the tree and its historical contribution."
    if children:
        text += (
            "\nDeleting this Card alone gives its children its parent. "
            "If it has no parent, its children become root-level."
        )
    await send_registered(
        context.message,
        context.services,
        text,
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
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
        blocking = await pending_checks(session, card_id)
    if blocking:
        # Done is gated: the user answers each Check on its own screen, and nothing
        # is written until Save, so backing out leaves the Card live.
        await render_check_resolution(
            context.message,
            context.services,
            card_id,
            back=Place("card_view", {"id": card_id}, context.back),
        )
        return
    async with context.sessions() as session:
        result = await finish_card(session, card_id)
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
    "card_view": _on_view,
    "card_children": _on_children,
    "card_move": _on_move,
    "card_edit_text": _on_edit_text,
    "card_open_schedule": _on_open_schedule,
    "card_set_field": _on_set_field,
    "card_toggle_field": _on_toggle_field,
    "card_archive": _on_archive,
    "card_restore": _on_restore,
    "card_delete_prompt": _on_delete_prompt,
    "card_delete_confirm": _on_delete_confirm,
    "card_finish": _on_finish,
    **{f"card_choose_{field}": _on_choices for field in CARD_CHOICE_FIELDS},
    **dict.fromkeys(CARD_RELATION_TOGGLES, _on_toggle_relation),
    **CARD_DRAFT_ACTIONS,
    **CARD_DONE_ACTIONS,
    **BOARD_ACTIONS,
}
