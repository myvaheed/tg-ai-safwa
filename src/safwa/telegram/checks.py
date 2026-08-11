from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select

from ..constants import CHECK_LIST_LIMIT
from ..domain import DomainError, card_checks, pending_checks
from ..enums import CHECK_OUTCOME_LABELS, CheckOutcome, MessageKind
from ..models import Card, Check, CheckTag, CheckValue, Tag, UiSession, Value
from ._core import Services
from ._messaging import edit_registered_message, send_registered, token_button
from ._presentation import with_notice

CHECK_STATUS_EMOJIS = {
    "pending": "⬜",
    CheckOutcome.PASSED.value: "✅",
    CheckOutcome.FAILED.value: "❌",
    CheckOutcome.NOT_APPLICABLE.value: "➖",
}
# Tapping walks the three settable answers; Pending is derived and cannot be returned to.
_OUTCOME_CYCLE = (
    CheckOutcome.PASSED.value,
    CheckOutcome.FAILED.value,
    CheckOutcome.NOT_APPLICABLE.value,
)


def check_status(check: Check) -> str:
    return check.outcome or "pending"


def check_status_label(check: Check) -> str:
    status = check_status(check)
    return f"{CHECK_STATUS_EMOJIS[status]} {CHECK_OUTCOME_LABELS[status]}"


def next_outcome(current: str | None) -> str:
    if current not in _OUTCOME_CYCLE:
        return _OUTCOME_CYCLE[0]
    return _OUTCOME_CYCLE[(_OUTCOME_CYCLE.index(current) + 1) % len(_OUTCOME_CYCLE)]


async def scoped_checks(session, scope: dict[str, Any]) -> list[Check]:
    """Checks reachable from one Card, Tag, or Value screen.

    A Card owns its Checks; a Tag or Value only classifies them, so those screens read
    through the link tables instead of the owning column.
    """
    kind = scope.get("kind")
    scope_id = int(scope["id"])
    if kind == "card":
        return await card_checks(session, scope_id)
    if kind == "tag":
        condition = Check.id.in_(select(CheckTag.check_id).where(CheckTag.tag_id == scope_id))
    elif kind == "value":
        condition = Check.id.in_(select(CheckValue.check_id).where(CheckValue.value_id == scope_id))
    else:
        raise DomainError("Unsupported Check scope")
    return list(
        await session.scalars(
            select(Check).where(condition, Check.archived_at.is_(None)).order_by(Check.id)
        )
    )


async def scope_title(session, scope: dict[str, Any]) -> str:
    model = {"card": Card, "tag": Tag, "value": Value}[scope["kind"]]
    entity = await session.get(model, int(scope["id"]))
    if entity is None:
        raise DomainError("This screen's item no longer exists")
    return str(getattr(entity, "title", None) or getattr(entity, "name", ""))


async def render_checks(
    message: Message,
    services: Services,
    scope: dict[str, Any],
    *,
    back: dict[str, Any],
    replace_message_id: int | None = None,
    notice: str | None = None,
) -> None:
    async with services.sessions() as session:
        owner_title = await scope_title(session, scope)
        checks = await scoped_checks(session, scope)
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
                        {"id": check.id, "scope": scope, "back": back},
                    )
                ]
            )
        if scope["kind"] == "card":
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        "➕ Add Check",
                        "check_create_prompt",
                        {"scope": scope, "back": back},
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
        related_id=int(scope["id"]),
    )


async def render_check(
    message: Message,
    services: Services,
    check_id: int,
    *,
    scope: dict[str, Any],
    back: dict[str, Any],
    replace_message_id: int | None = None,
    notice: str | None = None,
) -> None:
    async with services.sessions() as session:
        check = await session.get(Check, check_id)
        if check is None or check.archived_at is not None:
            raise DomainError("Check does not exist or is archived")
        value_names = list(
            await session.scalars(
                select(Value.name)
                .join(CheckValue, CheckValue.value_id == Value.id)
                .where(CheckValue.check_id == check.id)
                .order_by(Value.name)
            )
        )
        tag_names = list(
            await session.scalars(
                select(Tag.name)
                .join(CheckTag, CheckTag.tag_id == Tag.id)
                .where(CheckTag.check_id == check.id)
                .order_by(Tag.name)
            )
        )
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="check_editor",
                state={
                    "check_id": check.id,
                    "scope": scope,
                    "back": back,
                    "message_id": replace_message_id or message.message_id,
                },
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        payload = {"id": check.id, "scope": scope, "back": back}
        field_specs = [
            ("✏️ Title", "check_edit_text", {**payload, "field": "title"}),
            ("📝 Note", "check_edit_text", {**payload, "field": "note"}),
            (f"🔁 Repeat: {'On' if check.repeatable else 'Off'}", "check_toggle_repeat", payload),
            (f"{check_status_label(check)} → next", "check_cycle_status", payload),
        ]
        buttons = [await token_button(session, services.owner_id, *spec) for spec in field_specs]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "Archive Check", "check_archive", payload
                )
            ]
        )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "check_list_back",
                    {"scope": scope, "back": back},
                )
            ]
        )
        await session.commit()

    body = "\n".join(
        [
            f"<b>Check</b>: {html.escape(check.title)}",
            f"Status: {check_status_label(check)}",
            f"Note: {html.escape(check.note or '—')}",
            f"Repeatable: {'Yes' if check.repeatable else 'No'}",
            f"Card: {'#' + str(check.card_id) if check.card_id else '—'}",
            f"Values: {html.escape(', '.join(value_names) or '—')}",
            f"Tags: {html.escape(', '.join(tag_names) or '—')}",
        ]
    )
    await _deliver(
        message,
        services,
        with_notice(body, notice),
        InlineKeyboardMarkup(inline_keyboard=rows),
        replace_message_id,
        related_id=check.id,
    )


async def render_check_text_prompt(
    message: Message,
    services: Services,
    *,
    check_id: int | None,
    field: str,
    scope: dict[str, Any],
    back: dict[str, Any],
) -> None:
    if field not in {"title", "note"}:
        raise DomainError("Only a Check title or Note can be edited as text")
    async with services.sessions() as session:
        current = ""
        if check_id is not None:
            check = await session.get(Check, check_id)
            if check is None or check.archived_at is not None:
                raise DomainError("Check does not exist or is archived")
            current = getattr(check, field) or ""
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="check_text",
                state={
                    "check_id": check_id,
                    "field": field,
                    "scope": scope,
                    "back": back,
                    "message_id": message.message_id,
                },
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        cancel = await token_button(
            session,
            services.owner_id,
            "↩️ Back",
            "check_list_back",
            {"scope": scope, "back": back},
        )
        await session.commit()
    heading = "New Check title" if check_id is None else f"Set new {field.title()}"
    await send_registered(
        message,
        services,
        f"<b>Current {html.escape(field)}</b>: {html.escape(current or '—')}\n\n{heading}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[cancel]]),
    )


async def render_check_resolution(
    message: Message,
    services: Services,
    card_id: int,
    *,
    back: dict[str, Any],
    outcomes: dict[str, str] | None = None,
    replace_message_id: int | None = None,
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
        # Default to `failed` rather than `passed`: a one-tap "all done" would let the
        # gate be cleared by asserting Checks that never happened.
        state_outcomes = {
            str(check.id): (outcomes or {}).get(str(check.id), CheckOutcome.FAILED.value)
            for check in pending
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
            outcome = state_outcomes[str(check.id)]
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"{CHECK_STATUS_EMOJIS[outcome]} {check.title}"[:60],
                        "check_resolve_cycle",
                        {"card_id": card_id, "check_id": check.id},
                    )
                ]
            )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "✅ Save",
                    "check_resolve_save",
                    {"card_id": card_id},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "check_resolve_cancel",
                    {"id": card_id, "back": back},
                ),
            ]
        )
        await session.commit()

    lines = [
        f"<b>Pending Checks — {html.escape(card.title)}</b>",
        "Answer each Check before this Card is Done. Tap one to change its answer.",
        "",
        *[
            f"{CHECK_STATUS_EMOJIS[state_outcomes[str(check.id)]]} {html.escape(check.title)}"
            f" — {CHECK_OUTCOME_LABELS[state_outcomes[str(check.id)]]}"
            for check in pending
        ],
    ]
    await _deliver(
        message,
        services,
        "\n".join(lines),
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
    )
