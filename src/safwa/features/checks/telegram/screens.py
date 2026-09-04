"""The Checks on a Card, one Check, and the Values it measures."""

from __future__ import annotations

import html
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from tg_agent_shell.adapters.kinds import MessageKind
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.telegram import (
    Services,
    choice_rows,
    choice_screen,
    edit_registered_message,
    paginate,
    send_registered,
    token_button,
    with_notice,
)

from ....constants import CHECK_LIST_LIMIT, SELECTOR_PAGE_SIZE
from ....foundation.marks import live_repeat_instance_id, title_marks
from ...cards.api import card_labels, card_title
from ...values.model import Value
from ..model import CHECK_OUTCOME_LABELS, Check, CheckOutcome
from ..use_cases import card_checks, check_card_id, check_value_ids

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


async def deliver(
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
        titles: list[str] = []
        for check in shown:
            titles.append(check.title + await title_marks(session, check))
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"{CHECK_STATUS_EMOJIS[check_status(check)]} {titles[-1]}"[:60],
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
            f"{CHECK_STATUS_EMOJIS[check_status(check)]} {html.escape(title)}"
            f" — {CHECK_OUTCOME_LABELS[check_status(check)]}"
            for check, title in zip(shown, titles, strict=True)
        )
    if len(checks) > len(shown):
        lines.append(f"Showing the first {CHECK_LIST_LIMIT} of {len(checks)} Checks.")
    await deliver(
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
    replace: bool | None = None,
) -> None:
    """One Check with its answer buttons.

    ``card_id`` is only where Back returns to: a Check reached from the advisor, or one
    hanging on no Card at all, has no owning screen to go back to.
    """
    back = back or {}
    async with services.sessions() as session:
        check = await session.get(Check, check_id)
        if check is None:
            raise DomainError("Check does not exist")
        # An archived Check is read, not answered: it keeps the answer it was archived with.
        archived = check.archived_at is not None
        linked_card_id = await check_card_id(session, check.id)
        linked_card_ids = [linked_card_id] if linked_card_id is not None else []
        linked_value_ids = await check_value_ids(session, check.id)
        payload = {"id": check.id, "card_id": card_id, "back": back}
        current = check_status(check)
        # The owner sets only what they alone know: whether it repeats, and how it turned out.
        rows: list[list[InlineKeyboardButton]] = (
            []
            if archived
            else [
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
        )
        live_id = (
            await live_repeat_instance_id(session, check) if check.is_closed_repeat() else None
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
        if not archived:
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
        # Deleting is the owner's half of CH-DELETE-014, and it is what an archived Check
        # offers instead of the controls it no longer has.
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "🗑 Delete",
                    "check_delete_prompt",
                    payload,
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "check_list" if card_id is not None else "check_back",
                    {"card_id": card_id, "back": back},
                ),
            ]
        )
        card_titles = await card_labels(session, linked_card_ids)
        check_marks = await title_marks(session, check)
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
            f"<b>Check</b>: {html.escape(check.title + check_marks)}",
            f"Status: {check_status_label(check)}",
            f"Repeatable: {'Yes' if check.repeatable else 'No'}",
            f"Cards: {html.escape(', '.join(card_titles)) or '—'}",
            f"Values: {html.escape(', '.join(value_names)) or '—'}",
        ]
    )
    await deliver(
        message,
        services,
        with_notice(body, notice),
        InlineKeyboardMarkup(inline_keyboard=rows),
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
    back = back or {}
    payload = {"id": check_id, "card_id": card_id, "back": back}
    async with services.sessions() as session:
        check = await session.get(Check, check_id)
        if check is None or check.archived_at is not None:
            raise DomainError("Check does not exist or is archived")
        options = [
            (value.name, value.id)
            for value in await session.scalars(
                select(Value).order_by(Value.name)
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
