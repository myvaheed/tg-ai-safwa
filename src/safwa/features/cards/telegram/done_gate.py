"""The closing screen: answer every unobserved Check, set the time and the Effort Points, or go
back and leave the Card live (CD-DONE-071).

Finishing a Card is what opens this screen, so it belongs to Cards; the Check rows on it are
worded by the feature that owns Checks. What the owner set so far travels in every button of
the screen, so typing the time or choosing Effort leaves it and comes back to it. Nothing is
written until Save, so leaving here cannot half-finish the Card.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    TextInputScreen,
    choice_rows,
    choice_screen,
    edit_registered_message,
    edited_screen,
    go,
    menu_row,
    place_button,
    render_text_input,
    send_registered,
    with_notice,
)
from tg_agent_shell.telegram.contributions import TextInputFlow

from ...checks.model import PENDING, CheckOutcome
from ...checks.telegram import answer_button_label, check_line
from ...checks.use_cases import pending_checks
from ...profile.api import effort_tracking_on, time_tracking_on
from ..api import EFFORT_POINTS_ABOUT, EFFORT_POINTS_PAGE, live_card_title
from ..model import Card, CardKind, effort_label, minutes_label
from ..use_cases import finish_card, update_card_fields
from .selectors import CHOICE_TITLES, choice_options
from .text_input import card_text_prompt, parse_minutes

CLOSING = "card_closing"
_TIME_FLOW = "card_closing_time"
# What the screen carries besides the Card: the answers given, and the time and the Effort
# Points while each is on.
_STATE_KEYS = ("card_id", "answers", "minutes", "effort")


def _state(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in _STATE_KEYS if key in payload}


async def render_check_resolution(
    message: Message,
    services: Services,
    card_id: int,
    *,
    back: Place,
    outcomes: dict[str, str | None] | None = None,
    notice: str | None = None,
) -> None:
    """Open the closing screen of a Card. `back` is the Card."""
    closing = Place(CLOSING, {"card_id": card_id, "answers": dict(outcomes or {})}, back)
    await render_closing(message, services, closing, notice=notice)


async def render_closing(
    message: Message,
    services: Services,
    closing: Place,
    *,
    notice: str | None = None,
    replace_message_id: int | None = None,
) -> None:
    """The screen `closing` names: its Card's Pending Checks with their answers, then the
    time and the Effort Points while each is on, as the Card has them until they are set."""
    state = _state(closing.args)
    card_id = int(state["card_id"])
    async with services.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        card_name = await live_card_title(session, card_id)
        pending = await pending_checks(session, card_id)
        action = card.kind == CardKind.ACTION.value
        if action and await time_tracking_on(session):
            state.setdefault("minutes", card.tracked_mins)
        if action and await effort_tracking_on(session):
            state.setdefault("effort", card.effort_points)
        # Nothing is prefilled: a Check may only be cleared by an answer the user gave.
        answers: dict[str, str | None] = {
            str(check.id): (state.get("answers") or {}).get(str(check.id)) for check in pending
        }
        state["answers"] = answers
        here = closing.but(**state)
        # The buttons carry the answer, so several rows are told apart by the numbers in the text.
        numbers = [f"{index}. " if len(pending) > 1 else "" for index in range(1, len(pending) + 1)]
        rows: list[list[InlineKeyboardButton]] = []
        for number, check in zip(numbers, pending, strict=True):
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        answer_button_label(outcome, current=answers[str(check.id)], prefix=number),
                        Place(
                            "check_resolve_set",
                            {**state, "check_id": check.id, "outcome": outcome.value},
                            closing.back,
                        ),
                    )
                    for outcome in CheckOutcome
                ]
            )
        if "minutes" in state:
            minutes = state["minutes"]
            rows.append([await place_button(
                session, services.owner_id,
                f"⌛ Time spent: {minutes_label(minutes) if minutes else '—'}",
                here.child(_TIME_FLOW),
            )])
        if "effort" in state:
            rows.append([await place_button(
                session, services.owner_id,
                f"🔢 Effort: {effort_label(state['effort'])}",
                here.child("card_closing_effort"),
            )])
        closing_row = []
        if not pending or any(answers.values()):
            closing_row.append(
                await place_button(
                    session,
                    services.owner_id,
                    "✅ Save",
                    Place("check_resolve_save", state, closing.back),
                )
            )
        closing_row.append(
            await place_button(
                session,
                services.owner_id,
                "↩️ Back",
                Place("check_resolve_cancel", {}, closing.back),
            )
        )
        rows.append(closing_row)
        await session.commit()

    lines = [f"<b>Done — {html.escape(card_name)}</b>"]
    if pending:
        lines += [
            "Answer every Check before this Card is Done.",
            "",
            *[
                number + check_line(check.title, answers[str(check.id)] or PENDING)
                for number, check in zip(numbers, pending, strict=True)
            ],
        ]
    lines += ["", "Nothing is written until Save."]
    text = with_notice("\n".join(lines), notice)
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message, services, replace_message_id, text,
            kind=MessageKind.DASHBOARD, markup=markup, related_id=card_id,
        )
        return
    await send_registered(
        message, services, text, kind=MessageKind.DASHBOARD, markup=markup, related_id=card_id
    )


async def _on_view(context: CallbackContext) -> None:
    await render_closing(
        context.message,
        context.services,
        Place(CLOSING, _state(context.payload), context.back),
        notice=context.payload.get("notice"),
    )


async def _on_set(context: CallbackContext) -> None:
    state = _state(context.payload)
    state["answers"] = {
        **(state.get("answers") or {}),
        str(context.payload["check_id"]): CheckOutcome(str(context.payload["outcome"])).value,
    }
    await render_closing(context.message, context.services, Place(CLOSING, state, context.back))


async def _on_effort(context: CallbackContext) -> None:
    """Effort's choices, as the Card's own screen offers them, and the page on what they
    mean; a choice goes back to the closing screen with it."""
    closing = context.back
    if closing is None:
        raise DomainError("This screen expired")
    async with context.sessions() as session:
        options = await choice_options(session, "effort")
    choices = choice_rows(
        options, {closing.args.get("effort")}, lambda value: closing.but(effort=value)
    )
    choices.append((EFFORT_POINTS_ABOUT, context.place.child(EFFORT_POINTS_PAGE)))
    await choice_screen(
        context.message, context.services, CHOICE_TITLES["effort"], choices, back=closing
    )


async def _on_time(context: CallbackContext) -> None:
    closing = context.back
    if closing is None:
        raise DomainError("This screen expired")
    minutes = closing.args.get("minutes")
    title, instruction = card_text_prompt("tracked_mins", deadline=False)
    await render_text_input(
        context.message,
        context.services,
        screen=TextInputScreen(
            title=title,
            current_value=minutes_label(minutes) if minutes else "",
            instruction=instruction,
            back=closing,
            related_id=int(closing.args["card_id"]),
        ),
        state={"flow": _TIME_FLOW},
    )


async def _keep_typed(session: Any, services: Any, state: Mapping[str, Any], value: Any) -> None:
    """The time stays on the screen until Save writes it."""
    del session, services, state, value


async def _back_with_time(
    message: Any, services: Any, state: Mapping[str, Any], value: Any
) -> None:
    await render_closing(
        message,
        services,
        edited_screen(state).but(minutes=value),
        replace_message_id=int(state["text_input"]["message_id"]),
    )


CLOSING_TIME_INPUT = TextInputFlow(
    name=_TIME_FLOW,
    validator=lambda state: parse_minutes,
    apply=_keep_typed,
    render=_back_with_time,
)


async def _on_save(context: CallbackContext) -> None:
    state = _state(context.payload)
    card_id = int(state["card_id"])
    stored = dict(state.get("answers") or {})
    if any(value is None for value in stored.values()):
        await render_closing(
            context.message,
            context.services,
            Place(CLOSING, state, context.back),
            notice="Answer every Check before saving.",
        )
        return
    async with context.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        outcomes = {int(key): value for key, value in stored.items()}
        result = await finish_card(session, card_id, check_outcomes=outcomes)
        # Written once a repeating Action's next copy has taken the estimate, so it keeps it.
        kept = {"tracked_mins": state.get("minutes"), "effort_points": state.get("effort")}
        changed = {
            field: kept[field]
            for field, key in (("tracked_mins", "minutes"), ("effort_points", "effort"))
            if key in state and kept[field] != getattr(card, field)
        }
        if changed:
            await update_card_fields(session, card_id, changed)
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
    await go(context, context.back, notice="The Card is still live; its Checks were not changed.")


CARD_DONE_ACTIONS: dict[str, CallbackHandler] = {
    CLOSING: _on_view,
    "check_resolve_set": _on_set,
    "check_resolve_save": _on_save,
    "check_resolve_cancel": _on_cancel,
    "card_closing_effort": _on_effort,
    _TIME_FLOW: _on_time,
}
