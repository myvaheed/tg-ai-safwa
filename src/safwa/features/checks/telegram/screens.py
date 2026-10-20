"""The Checks on a Card, one Check, and the Values it measures."""

from __future__ import annotations

import html
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_llm import markdown_to_telegram_html
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Place,
    Services,
    back_button,
    choice_rows,
    choice_screen,
    edit_registered_message,
    paginate,
    place_button,
    place_link,
    render_citations,
    send_registered,
    with_notice,
)

from ....constants import SELECTOR_PAGE_SIZE
from ....foundation.marks import live_repeat_instance_id, title_marks
from ...cards.api import card_labels, card_title
from ...schedules.api import schedule_summary, workspace_zone
from ...values.model import Value
from ..model import CHECK_OUTCOME_LABELS, PENDING, Check, CheckOutcome
from ..use_cases import (
    answered_checks,
    card_checks,
    check_answer_counts,
    check_card_id,
    check_value_ids,
    schedulable,
)

# How many Checks one result page lists.
CHECK_LIST_LIMIT = 25
LINKED_CHECKS_LIMIT = 5

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


async def linked_check_links(
    session: AsyncSession, services: Services, checks: list[Check], here: Place, all_checks: Place
) -> list[str]:
    """Five direct Check links with answer totals, then Others linking to the whole result."""
    shown = checks[:LINKED_CHECKS_LIMIT]
    counts = await check_answer_counts(session, shown)
    links = []
    for check in shown:
        title = html.escape(check.title + await title_marks(session, check))
        link = await place_link(session, services, title, here.child("check_view", id=check.id))
        passed, missed = counts[check.id]
        links.append(f"• {link} ✅{passed} · ❌{missed}")
    if len(checks) > len(shown):
        link = await place_link(session, services, f"Others({len(checks) - len(shown)})", all_checks)
        links.append(f"• {link}")
    return links


async def check_results(
    session: AsyncSession, services: Services, checks: list[Check], here: Place, *, page: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    """All attached Checks in pages, with Refresh and the way back."""
    current = paginate(checks, page, CHECK_LIST_LIMIT)
    if current.index:
        here = here.but(page=current.index)
    rows = []
    lines = [f"{len(checks)} matching check{'s' if len(checks) != 1 else ''}"]
    if current.count > 1:
        lines[0] += f" · {current.label}"
    for check in current.items:
        title = check.title + await title_marks(session, check)
        lines.append(check_line(title, check.status))
        rows.append(
            [
                await place_button(
                    session,
                    services.owner_id,
                    f"{CHECK_STATUS_EMOJIS[check.status]} {title}"[:60],
                    here.child("check_view", id=check.id),
                )
            ]
        )
    if not checks:
        lines.append("No Checks yet.")
    pages = [
        await place_button(session, services.owner_id, label, here.but(page=index))
        for label, index in (("←", current.index - 1), ("→", current.index + 1))
        if 0 <= index < current.count
    ]
    if pages:
        rows.append(pages)
    rows.append([await place_button(session, services.owner_id, "↻ Refresh", here)])
    rows.append([await back_button(session, services.owner_id, here.back)])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def render_answered_checks(
    message: Message, services: Services, check_id: int, *, back: Place | None = None, page: int = 0
) -> None:
    """Every answered instance of one Check series, in pages, newest answer first."""
    async with services.sessions() as session:
        check = await session.get(Check, check_id)
        if check is None:
            raise DomainError("Check does not exist")
        answers = await answered_checks(session, check)
        current = paginate(answers, page)
        here = Place("check_answered", {"id": check.id}, back)
        if current.index:
            here = here.but(page=current.index)
        tz = await workspace_zone(session)
        rows = []
        for answer in current.items:
            timestamp = f"{answer.resolved_at.astimezone(tz):%Y-%m-%d %H:%M}"
            title = answer.title + await title_marks(session, answer)
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        f"{CHECK_STATUS_EMOJIS[answer.status]} {timestamp} · {title}"[:60],
                        here.child("check_view", id=answer.id),
                    )
                ]
            )
        pages = []
        for label, index in (("←", current.index - 1), ("→", current.index + 1)):
            if 0 <= index < current.count:
                pages.append(
                    await place_button(session, services.owner_id, label, here.but(page=index))
                )
        if pages:
            rows.append(pages)
        rows.append([await place_button(session, services.owner_id, "↻ Refresh", here)])
        rows.append([await back_button(session, services.owner_id, back)])
        await session.commit()
    count = f"{len(answers)} matching check{'s' if len(answers) != 1 else ''}"
    await send_registered(
        message,
        services,
        f"<b>Answered: {html.escape(check.title)}</b>\n\n{count} · {current.label}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=check.id,
    )


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
    back: Place | None,
    replace_message_id: int | None = None,
    notice: str | None = None,
    page: int = 0,
) -> None:
    """The Checks on one Card. `back` is that Card's screen."""
    here = Place("check_list", {"card_id": card_id}, back)
    async with services.sessions() as session:
        owner_title = await card_title(session, card_id)
        checks = await card_checks(session, card_id)
        body, markup = await check_results(session, services, checks, here, page=page)
        await session.commit()

    await deliver(
        message,
        services,
        with_notice(f"<b>Checks — {html.escape(owner_title)}</b>\n{body}", notice),
        markup,
        replace_message_id,
        related_id=card_id,
    )


async def render_check(
    message: Message,
    services: Services,
    check_id: int,
    *,
    back: Place | None = None,
    replace_message_id: int | None = None,
    notice: str | None = None,
    replace: bool | None = None,
) -> None:
    """One Check with its answer buttons. `back` is where it was opened from: a Check
    reached from the advisor has nothing behind it."""
    here = Place("check_view", {"id": check_id}, back)
    async with services.sessions() as session:
        check = await session.get(Check, check_id)
        if check is None:
            raise DomainError("Check does not exist")
        # An archived Check is read, not answered: it keeps the answer it was archived with.
        archived = check.archived_at is not None
        linked_card_id = await check_card_id(session, check.id)
        linked_card_ids = [linked_card_id] if linked_card_id is not None else []
        linked_value_ids = await check_value_ids(session, check.id)
        rows: list[list[InlineKeyboardButton]] = []
        if not archived:
            if check.outcome is None and await schedulable(session, check.id):
                rows.append(
                    [
                        await place_button(
                            session,
                            services.owner_id,
                            "⏱ Schedule",
                            Place("check_open_schedule", {"id": check.id}, back),
                        )
                    ]
                )
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        answer_button_label(outcome, current=check.status),
                        Place(
                            "check_set_status", {"id": check.id, "outcome": outcome.value}, back
                        ),
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
                    await place_button(
                        session,
                        services.owner_id,
                        f"🔄 Current: {live_check.title}"[:60],
                        here.child("check_view", id=live_check.id),
                    )
                ]
            )
        passed, missed = (await check_answer_counts(session, [check]))[check.id]
        if passed or missed:
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        f"Answered: ✅{passed} · ❌{missed}",
                        here.child("check_answered", id=check.id),
                    )
                ]
            )
        if not archived:
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        "📝 Note",
                        Place("check_edit_note", {"id": check.id}, back),
                    ),
                    await place_button(
                        session,
                        services.owner_id,
                        "💎 Values",
                        Place("check_choose_values", {"id": check.id}, back),
                    )
                ]
            )
        # Deleting is the owner's half of CH-DELETE-014, and it is what an archived Check
        # offers instead of the controls it no longer has.
        rows.append(
            [
                await place_button(
                    session,
                    services.owner_id,
                    "🗑 Delete",
                    Place("check_delete_prompt", {"id": check.id}, back),
                ),
                await back_button(session, services.owner_id, back),
            ]
        )
        card_titles = await card_labels(session, linked_card_ids)
        check_marks = await title_marks(session, check)
        value_names = (
            [
                value.name
                for value in await session.scalars(
                    select(Value).where(Value.id.in_(linked_value_ids)).order_by(Value.order)
                )
            ]
            if linked_value_ids
            else []
        )
        note = await render_citations(session, services, markdown_to_telegram_html(check.note or "—"), here=here)
        await session.commit()

    body = "\n".join(
        [
            f"<b>Check</b>: {html.escape(check.title + check_marks)}",
            f"\n<i>Note:</i>\n{note}\n",
            f"<i>Status:</i> {CHECK_STATUS_EMOJIS[check.status]} {CHECK_OUTCOME_LABELS[check.status]}",
            f"<i>Schedule:</i> {html.escape(check.schedule or '—')}",
            f"<i>Card:</i> {html.escape(', '.join(card_titles)) or '—'}",
            f"<i>Values:</i> {html.escape(', '.join(value_names)) or '—'}",
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


async def value_options(session: AsyncSession) -> list[tuple[str, Any]]:
    """Every Value a Check may measure, in the order the owner ranks them."""
    return [
        (value.name, value.id)
        for value in await session.scalars(select(Value).order_by(Value.order))
    ]


async def render_check_values(
    message: Message,
    services: Services,
    check_id: int,
    *,
    back: Place | None = None,
    page: int = 0,
) -> None:
    """Choose which Values this Check measures. Its Cards are chosen elsewhere. `back` is
    where the Check was opened from."""
    async with services.sessions() as session:
        check = await session.get(Check, check_id)
        if check is None or check.archived_at is not None:
            raise DomainError("Check does not exist or is archived")
        options = await value_options(session)
        current = paginate(options, page, SELECTOR_PAGE_SIZE)
        selected = set(await check_value_ids(session, check.id))
        choices = choice_rows(
            current.items,
            selected,
            # The page rides along, so ticking one on page 2 comes back to page 2.
            lambda value_id: Place(
                "check_toggle_value", {"id": check_id, "value_id": value_id, "page": page}, back
            ),
        )
        await session.commit()
    await choice_screen(
        message,
        services,
        "Values",
        choices,
        back=Place("check_view", {"id": check_id}, back),
        paging=(current, Place("check_choose_values", {"id": check_id}, back)),
    )
