"""The Done-gate: answer every unobserved Check, or go back and leave the Card live."""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete

from ....foundation.errors import DomainError
from ....models import UiSession
from ....shell import Services, token_button, with_notice
from ...cards.api import live_card_title
from ..model import CHECK_OUTCOME_LABELS
from ..use_cases import unobserved_series
from .screens import CHECK_STATUS_EMOJIS, SETTABLE_OUTCOMES, deliver, outcome_button_label


async def render_check_resolution(
    message: Message,
    services: Services,
    card_id: int,
    *,
    back: dict[str, Any],
    outcomes: dict[str, str | None] | None = None,
    replace_message_id: int | None = None,
    notice: str | None = None,
) -> None:
    """A repeating series already answered on this Card is not asked again: its open
    instance belongs to the next cycle. Nothing is written until Save, so leaving here
    cannot half-finish the Card.
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
                    "message_id": replace_message_id or message.message_id,
                },
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
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
    await deliver(
        message,
        services,
        with_notice("\n".join(lines), notice),
        InlineKeyboardMarkup(inline_keyboard=rows),
        replace_message_id,
        related_id=card_id,
    )
