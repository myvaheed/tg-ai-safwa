from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select

from ..constants import CHECK_LIST_LIMIT, SELECTOR_PAGE_SIZE
from ..domain import (
    DomainError,
    card_checks,
    check_card_ids,
    check_value_ids,
    is_closed_repeat,
    live_repeat_instance_id,
    pending_checks,
)
from ..enums import CHECK_OUTCOME_LABELS, CheckOutcome, MessageKind
from ..models import Card, Check, UiSession, Value
from ._core import Services
from ._messaging import edit_registered_message, send_registered, token_button
from ._presentation import paginate, with_notice
from .cards import choice_rows, choice_screen

CHECK_STATUS_EMOJIS = {
    "pending": "⬜",
    CheckOutcome.PASSED.value: "✅",
    CheckOutcome.MISSED.value: "❌",
}
# One button per answer; Pending is derived and cannot be set.
SETTABLE_OUTCOMES = (
    CheckOutcome.PASSED.value,
    CheckOutcome.MISSED.value,
)


def check_status(check: Check) -> str:
    return check.outcome or "pending"


def check_status_label(check: Check) -> str:
    status = check_status(check)
    return f"{CHECK_STATUS_EMOJIS[status]} {CHECK_OUTCOME_LABELS[status]}"


def outcome_button_label(outcome: str, title: str, *, current: str | None) -> str:
    marker = "• " if outcome == current else ""
    return f"{marker}{CHECK_STATUS_EMOJIS[outcome]} {title}"[:60]


async def card_title(session, card_id: int) -> str:
    card = await session.get(Card, card_id)
    if card is None or card.archived_at is not None:
        raise DomainError("Card does not exist or is archived")
    return str(card.title)


async def render_checks(
    message: Message,
    services: Services,
    card_id: int,
    *,
    back: dict[str, Any],
    replace_message_id: int | None = None,
    notice: str | None = None,
) -> None:
    async with services.sessions() as session:
        owner_title = await card_title(session, card_id)
        checks = await card_checks(session, card_id)
        shown = checks[:CHECK_LIST_LIMIT]
        rows: list[list[InlineKeyboardButton]] = []
        for check in shown:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"{CHECK_STATUS_EMOJIS[check_status(check)]} {check.title}"[:60],
                        "check_view",
                        {"id": check.id, "card_id": card_id, "back": back},
                    )
                ]
            )
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "↩️ Back", "check_back", {"back": back}
                )
            ]
        )
        await session.commit()

    lines = [f"<b>Checks — {html.escape(owner_title)}</b>"]
    if not shown:
        lines.append("No Checks yet.")
    else:
        lines.extend(
            f"{CHECK_STATUS_EMOJIS[check_status(check)]} {html.escape(check.title)}"
            f" — {CHECK_OUTCOME_LABELS[check_status(check)]}"
            for check in shown
        )
    if len(checks) > len(shown):
        lines.append(f"Showing the first {CHECK_LIST_LIMIT} of {len(checks)} Checks.")
    await _deliver(
        message,
        services,
        with_notice("\n".join(lines), notice),
        InlineKeyboardMarkup(inline_keyboard=rows),
        replace_message_id,
        related_id=card_id,
    )


async def render_check(
    message: Message,
    services: Services,
    check_id: int,
    *,
    card_id: int | None = None,
    back: dict[str, Any] | None = None,
    replace_message_id: int | None = None,
    notice: str | None = None,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
    replace: bool | None = None,
) -> None:
    """One Check with its answer buttons.

    ``card_id`` is only where Back returns to: a Check reached from the advisor, or one
    hanging on no Card at all, has no owning screen to go back to.
    """
    back = back or {"kind": "home"}
    async with services.sessions() as session:
        check = await session.get(Check, check_id)
        if check is None or check.archived_at is not None:
            raise DomainError("Check does not exist or is archived")
        linked_card_ids = await check_card_ids(session, check.id)
        linked_value_ids = await check_value_ids(session, check.id)
        payload = {"id": check.id, "card_id": card_id, "back": back}
        current = check_status(check)
        # The owner sets only what they alone know: whether it repeats, and how it turned out.
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"🔁 Repeat: {'On' if check.repeatable else 'Off'}",
                    "check_toggle_repeat",
                    payload,
                )
            ],
            [
                await token_button(
                    session,
                    services.owner_id,
                    outcome_button_label(
                        outcome, CHECK_OUTCOME_LABELS[outcome], current=current
                    ),
                    "check_set_status",
                    {**payload, "outcome": outcome},
                )
                for outcome in SETTABLE_OUTCOMES
            ],
        ]
        live_id = (
            await live_repeat_instance_id(session, check) if is_closed_repeat(check) else None
        )
        live_check = await session.get(Check, live_id) if live_id is not None else None
        if live_check is not None:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"🔄 Current: {live_check.title}"[:60],
                        "check_view",
                        {"id": live_check.id, "card_id": card_id, "back": back},
                    )
                ]
            )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "💎 Values",
                    "check_choose_values",
                    payload,
                )
            ]
        )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "check_list_back" if card_id is not None else "check_back",
                    {"card_id": card_id, "back": back},
                )
            ]
        )
        linked_cards = (
            list(
                await session.scalars(
                    select(Card).where(
                        Card.id.in_(linked_card_ids), Card.archived_at.is_(None)
                    )
                )
            )
            if linked_card_ids
            else []
        )
        card_titles = [card.title for card in linked_cards]
        value_names = (
            [
                value.name
                for value in await session.scalars(
                    select(Value).where(Value.id.in_(linked_value_ids)).order_by(Value.name)
                )
            ]
            if linked_value_ids
            else []
        )
        await session.commit()

    body = "\n".join(
        [
            f"<b>Check</b>: {html.escape(check.title)}",
            f"Status: {check_status_label(check)}",
            f"Repeatable: {'Yes' if check.repeatable else 'No'}",
            f"Cards: {html.escape(', '.join(card_titles)) or '—'}",
            f"Values: {html.escape(', '.join(value_names)) or '—'}",
        ]
    )
    await _deliver(
        message,
        services,
        with_notice(body, notice),
        InlineKeyboardMarkup(inline_keyboard=rows + list(extra_rows or [])),
        replace_message_id,
        related_id=check.id,
        replace=replace,
    )


async def render_check_values(
    message: Message,
    services: Services,
    check_id: int,
    *,
    card_id: int | None = None,
    back: dict[str, Any] | None = None,
    page: int = 0,
) -> None:
    """Choose which Values this Check measures. Its Cards are chosen elsewhere."""
    back = back or {"kind": "home"}
    payload = {"id": check_id, "card_id": card_id, "back": back}
    async with services.sessions() as session:
        check = await session.get(Check, check_id)
        if check is None or check.archived_at is not None:
            raise DomainError("Check does not exist or is archived")
        options = [
            (value.name, value.id)
            for value in await session.scalars(
                select(Value).where(Value.archived_at.is_(None)).order_by(Value.name)
            )
        ]
        current = paginate(options, page, SELECTOR_PAGE_SIZE)
        selected = set(await check_value_ids(session, check.id))
        choices = choice_rows(
            current.items,
            selected,
            # The page rides along, so ticking one on page 2 comes back to page 2.
            lambda value_id: ("check_toggle_value", {**payload, "value_id": value_id, "page": page}),
        )
        await session.commit()
    await choice_screen(
        message,
        services,
        "Values",
        choices,
        back=("↩️ Back", "check_view", payload),
        paging=(current, "check_choose_values", payload),
    )


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
    """The Done-gate screen: answer every Pending Check, or go back and stay live.

    Nothing is written until Save, so leaving here cannot half-finish the Card.
    """
    async with services.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None or card.archived_at is not None:
            raise DomainError("Card does not exist or is archived")
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
        f"<b>Pending Checks — {html.escape(card.title)}</b>",
        "Answer every Check before this Card is Done.",
        "",
        *[
            f"{CHECK_STATUS_EMOJIS[state_outcomes[str(check.id)] or 'pending']}"
            f" {html.escape(check.title)}"
            f" — {CHECK_OUTCOME_LABELS[state_outcomes[str(check.id)] or 'pending']}"
            for check in pending
        ],
    ]
    await _deliver(
        message,
        services,
        with_notice("\n".join(lines), notice),
        InlineKeyboardMarkup(inline_keyboard=rows),
        replace_message_id,
        related_id=card_id,
    )


async def _deliver(
    message: Message,
    services: Services,
    text: str,
    markup: InlineKeyboardMarkup,
    replace_message_id: int | None,
    *,
    related_id: int | None,
    replace: bool | None = None,
) -> None:
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=related_id,
        )
        return
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=markup,
        related_id=related_id,
        replace=replace,
    )
