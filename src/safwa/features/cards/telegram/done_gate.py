"""The Done-gate: answer every unobserved Check, or go back and leave the Card live.

Finishing a Card is what opens this screen, so it belongs to Cards; the rows on it are
Checks, and their wording comes from the feature that owns them. Nothing is written until
Save, so leaving here cannot half-finish the Card.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    go,
    menu_row,
    place_button,
    send_registered,
    token_button,
    with_notice,
)
from tg_agent_shell.telegram.model import UiSession

from ...checks.model import PENDING, CheckOutcome
from ...checks.telegram import answer_button_label, check_line
from ...checks.use_cases import pending_checks
from ..api import live_card_title
from ..use_cases import finish_card

_GATE_TTL = timedelta(minutes=30)


async def render_check_resolution(
    message: Message,
    services: Services,
    card_id: int,
    *,
    back: Place,
    outcomes: dict[str, str | None] | None = None,
    notice: str | None = None,
) -> None:
    """Answer every Pending linked Check before completing its Card. `back` is the Card."""
    async with services.sessions() as session:
        card_name = await live_card_title(session, card_id)
        pending = await pending_checks(session, card_id)
        if not pending:
            raise DomainError("This Card has no Pending Checks")
        # Nothing is prefilled: the gate may only be cleared by an answer the user gave.
        state_outcomes: dict[str, str | None] = {
            str(check.id): (outcomes or {}).get(str(check.id)) for check in pending
        }
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="check_resolve",
                state={
                    "card_id": card_id,
                    "outcomes": state_outcomes,
                    "back": back.address,
                    "message_id": message.message_id,
                },
                expires_at=datetime.now(UTC) + _GATE_TTL,
            )
        )
        # The buttons carry the answer, so several rows are told apart by the numbers in the text.
        numbers = [f"{index}. " if len(pending) > 1 else "" for index in range(1, len(pending) + 1)]
        rows: list[list[InlineKeyboardButton]] = []
        for number, check in zip(numbers, pending, strict=True):
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        answer_button_label(
                            outcome, current=state_outcomes[str(check.id)], prefix=number
                        ),
                        "check_resolve_set",
                        {"card_id": card_id, "check_id": check.id, "outcome": outcome.value},
                    )
                    for outcome in CheckOutcome
                ]
            )
        closing = []
        if any(state_outcomes.values()):
            closing.append(
                await token_button(
                    session,
                    services.owner_id,
                    "✅ Save",
                    "check_resolve_save",
                    {"card_id": card_id},
                )
            )
        closing.append(
            await place_button(
                session, services.owner_id, "↩️ Back", Place("check_resolve_cancel", {}, back)
            )
        )
        rows.append(closing)
        await session.commit()

    lines = [
        f"<b>Pending Checks — {html.escape(card_name)}</b>",
        "Answer every Check before this Card is Done.",
        "",
        *[
            number + check_line(check.title, state_outcomes[str(check.id)] or PENDING)
            for number, check in zip(numbers, pending, strict=True)
        ],
    ]
    await send_registered(
        message,
        services,
        with_notice("\n".join(lines), notice),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=card_id,
    )


async def _gate_state(context: CallbackContext) -> dict[str, Any]:
    async with context.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        if editor is None or editor.kind != "check_resolve":
            raise DomainError("This Check screen expired")
        return dict(editor.state)


async def _on_set(context: CallbackContext) -> None:
    state = await _gate_state(context)
    outcomes = dict(state.get("outcomes") or {})
    outcomes[str(context.payload["check_id"])] = CheckOutcome(
        str(context.payload["outcome"])
    ).value
    await render_check_resolution(
        context.message,
        context.services,
        int(context.payload["card_id"]),
        back=Place.at(state["back"]),
        outcomes=outcomes,
    )


async def _on_save(context: CallbackContext) -> None:
    card_id = int(context.payload["card_id"])
    state = await _gate_state(context)
    stored = dict(state.get("outcomes") or {})
    if any(value is None for value in stored.values()):
        await render_check_resolution(
            context.message,
            context.services,
            card_id,
            back=Place.at(state["back"]),
            outcomes=stored,
            notice="Answer every Check before saving.",
        )
        return
    async with context.sessions() as session:
        outcomes = {int(key): value for key, value in stored.items()}
        result = await finish_card(session, card_id, check_outcomes=outcomes)
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
    notice = "⚠️ " + "; ".join(result.warnings) if result.warnings else None
    await send_registered(
        context.message,
        context.services,
        with_notice("Card updated.", notice),
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def _on_cancel(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
    await go(context, context.back, notice="The Card is still live; its Checks were not changed.")


CARD_DONE_ACTIONS: dict[str, CallbackHandler] = {
    "check_resolve_set": _on_set,
    "check_resolve_save": _on_save,
    "check_resolve_cancel": _on_cancel,
}
