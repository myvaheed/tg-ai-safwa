"""The Checks on a Card, one Check, and the Values it measures."""

from __future__ import annotations

import html
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Services,
    back_button,
    choice_rows,
    choice_screen,
    edit_registered_message,
    paginate,
    send_registered,
    token_button,
    with_notice,
)

from ....constants import SELECTOR_PAGE_SIZE
from ....foundation.marks import live_repeat_instance_id, title_marks
from ...cards.api import card_labels, card_title
from ...schedules.api import schedule_summary
from ...values.model import Value
from ..model import CHECK_OUTCOME_LABELS, PENDING, Check, CheckOutcome
from ..use_cases import card_checks, check_card_id, check_value_ids

# How many Checks one Card's screen lists before it only counts the rest.
CHECK_LIST_LIMIT = 25

CHECK_STATUS_EMOJIS = {
    PENDING: "⬜",
    CheckOutcome.PASSED.value: "✅",
    CheckOutcome.MISSED.value: "❌",
}
# A Check is a question, so its buttons reply to it; the answer stored is Passed or Missed.
ANSWER_BUTTON_LABELS = {
    CheckOutcome.PASSED.value: "Yes",
    CheckOutcome.MISSED.value: "No",
}


def check_line(title: str, status: str) -> str:
    """One Check in a list: its status mark, its title and its status."""
    return f"{CHECK_STATUS_EMOJIS[status]} {html.escape(title)} — {CHECK_OUTCOME_LABELS[status]}"


def answer_button_label(outcome: str, *, current: str | None, prefix: str = "") -> str:
    """One answer button; the chosen answer is marked. `prefix` tells rows apart."""
    marker = "• " if outcome == current else ""
    return f"{prefix}{marker}{CHECK_STATUS_EMOJIS[outcome]} {ANSWER_BUTTON_LABELS[outcome]}"


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
                        f"{CHECK_STATUS_EMOJIS[check.status]} {titles[-1]}"[:60],
                        "check_view",
                        {"id": check.id, "card_id": card_id, "back": back},
                    )
                ]
            )
        rows.append([await back_button(session, services.owner_id, "check_back", back)])
        await session.commit()

    lines = [f"<b>Checks — {html.escape(owner_title)}</b>"]
    if not shown:
        lines.append("No Checks yet.")
    else:
        lines.extend(
            check_line(title, check.status) for check, title in zip(shown, titles, strict=True)
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
        rows: list[list[InlineKeyboardButton]] = []
        if not archived:
            if linked_card_id is None and check.outcome is None:
                rows.append(
                    [
                        await token_button(
                            session, services.owner_id, "⏱ Schedule", "check_open_schedule", payload
                        )
                    ]
                )
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        answer_button_label(outcome, current=check.status),
                        "check_set_status",
                        {**payload, "outcome": outcome.value},
                    )
                    for outcome in CheckOutcome
                ]
            )
        summary = await schedule_summary(session, check)
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
                    "check_list",
                    {"card_id": card_id, "back": back},
                )
                if card_id is not None
                else await back_button(session, services.owner_id, "check_back", back),
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
            f"Status: {CHECK_STATUS_EMOJIS[check.status]} {CHECK_OUTCOME_LABELS[check.status]}",
            f"Schedule: {html.escape(check.schedule or '—')}",
            f"Card: {html.escape(', '.join(card_titles)) or '—'}",
            f"Values: {html.escape(', '.join(value_names)) or '—'}",
        ]
    )
    if summary:
        body += "\n" + html.escape(summary)
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
