"""The Done-gate: answer every unobserved Check, or go back and leave the Card live.

Finishing an Action is what opens this screen, so it belongs to Cards; the rows on it are
Checks, and their wording comes from the feature that owns them. Nothing is written until
Save, so leaving here cannot half-finish the Card.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select

from ....adapters.kinds import MessageKind
from ....foundation.errors import DomainError
from ....shell import (
    CallbackContext,
    CallbackHandler,
    Services,
    menu_row,
    send_registered,
    token_button,
    with_notice,
)
from ....shell.model import UiSession
from ...checks.api import CheckOutcome
from ...checks.telegram import (
    CHECK_OUTCOME_LABELS,
    CHECK_STATUS_EMOJIS,
    SETTABLE_OUTCOMES,
    outcome_button_label,
)
from ...checks.use_cases import unobserved_series
from ..api import live_card_title
from ..model import CardStage
from ..use_cases import finish_action
from .screens import render_card

_GATE_TTL = timedelta(minutes=30)


async def render_check_resolution(
    message: Message,
    services: Services,
    card_id: int,
    *,
    back: dict[str, Any],
    outcomes: dict[str, str | None] | None = None,
    notice: str | None = None,
) -> None:
    """A repeating series already answered on this Card is not asked again: its open
    instance belongs to the next cycle.
    """
    async with services.sessions() as session:
        card_name = await live_card_title(session, card_id)
        pending = await unobserved_series(session, card_id)
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
                    "back": back,
                    "message_id": message.message_id,
                },
                expires_at=datetime.now(UTC) + _GATE_TTL,
            )
        )
        rows: list[list[InlineKeyboardButton]] = []
        for check in pending:
            current = state_outcomes[str(check.id)]
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        outcome_button_label(outcome, check.title, current=current),
                        "check_resolve_set",
                        {"card_id": card_id, "check_id": check.id, "outcome": outcome},
                    )
                    for outcome in SETTABLE_OUTCOMES
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
            await token_button(
                session,
                services.owner_id,
                "↩️ Back",
                "check_resolve_cancel",
                {"id": card_id, "back": back},
            )
        )
        rows.append(closing)
        await session.commit()

    lines = [
        f"<b>Pending Checks — {html.escape(card_name)}</b>",
        "Answer every Check before this Card is Done.",
        "",
        *[
            f"{CHECK_STATUS_EMOJIS[state_outcomes[str(check.id)] or 'pending']}"
            f" {html.escape(check.title)}"
            f" — {CHECK_OUTCOME_LABELS[state_outcomes[str(check.id)] or 'pending']}"
            for check in pending
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
        back=dict(state.get("back") or {}),
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
            back=dict(state.get("back") or {}),
            outcomes=stored,
            notice="Answer every Check before saving.",
        )
        return
    async with context.sessions() as session:
        outcomes = {int(key): value for key, value in stored.items()}
        result = await finish_action(session, card_id, CardStage.DONE, check_outcomes=outcomes)
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
    await render_card(
        context.message,
        context.services,
        int(context.payload["id"]),
        notice="The Card is still live; its Checks were not changed.",
    )


CARD_DONE_ACTIONS: dict[str, CallbackHandler] = {
    "check_resolve_set": _on_set,
    "check_resolve_save": _on_save,
    "check_resolve_cancel": _on_cancel,
}
