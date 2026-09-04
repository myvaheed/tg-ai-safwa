"""Reusable Telegram text-input screen with validation and retry behaviour."""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import delete

from ..adapters.kinds import MessageKind
from ..foundation.errors import DomainError
from .chat import delete_text_input, edit_registered_message, token_button
from .model import UiSession
from .services import Services

_DEFAULT_TTL = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class TextInputAction:
    """An optional non-mutating route available beside the universal Back button."""

    text: str
    action: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TextInputScreen:
    """The display and navigation contract for one editable text value."""

    title: str
    current_value: str
    instruction: str
    back_action: str
    back_payload: dict[str, Any]
    related_id: int | None = None
    ttl: timedelta = _DEFAULT_TTL
    extra_actions: tuple[TextInputAction, ...] = ()


type TextValidator[T] = Callable[[str], T]


def validate_text_input[T](raw: str, validator: TextValidator[T] | None = None) -> T | str:
    """Trim a submitted value, then run the caller's field-specific validator."""

    value = raw.strip()
    return validator(value) if validator is not None else value


def required_text(field_name: str) -> TextValidator[str]:
    """Build a precise reusable validator for fields that cannot be blank."""

    def validate(value: str) -> str:
        if not value:
            raise ValueError(f"{field_name} cannot be empty.")
        return value

    return validate


def _screen_text(screen: TextInputScreen, notice: str | None) -> str:
    current = screen.current_value or "—"
    parts = [
        f"<b>{html.escape(screen.title)}</b>",
        f"Current value:\n<pre>{html.escape(current)}</pre>",
    ]
    if notice:
        parts.append(f"⚠️ {html.escape(notice)}")
    parts.append(html.escape(screen.instruction))
    return "\n\n".join(parts)


def _screen_from_state(state: dict[str, Any]) -> TextInputScreen:
    saved = state.get("text_input")
    if not isinstance(saved, dict):
        raise ValueError("Text input state is missing")
    return TextInputScreen(
        title=str(saved["title"]),
        current_value=str(saved["current_value"]),
        instruction=str(saved["instruction"]),
        back_action=str(saved["back_action"]),
        back_payload=dict(saved.get("back_payload") or {}),
        related_id=saved.get("related_id"),
        ttl=timedelta(seconds=int(saved.get("ttl_seconds", _DEFAULT_TTL.total_seconds()))),
        extra_actions=tuple(
            TextInputAction(
                text=str(action["text"]),
                action=str(action["action"]),
                payload=dict(action.get("payload") or {}),
            )
            for action in saved.get("extra_actions", [])
        ),
    )


async def render_text_input(
    message: Message,
    services: Services,
    *,
    screen: TextInputScreen,
    state: dict[str, Any],
    notice: str | None = None,
) -> None:
    """Replace the originating view with a copyable current value and Back button."""

    prior = state.get("text_input")
    prior_message_id = prior.get("message_id") if isinstance(prior, dict) else None
    message_id = int(prior_message_id or message.message_id)
    next_state = {
        **state,
        "text_input": {
            "message_id": message_id,
            "title": screen.title,
            "current_value": screen.current_value,
            "instruction": screen.instruction,
            "back_action": screen.back_action,
            "back_payload": screen.back_payload,
            "related_id": screen.related_id,
            "ttl_seconds": int(screen.ttl.total_seconds()),
            "extra_actions": [
                {"text": action.text, "action": action.action, "payload": action.payload}
                for action in screen.extra_actions
            ],
        },
    }
    async with services.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="text_input",
                state=next_state,
                expires_at=datetime.now(UTC) + screen.ttl,
            )
        )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    action.text,
                    action.action,
                    action.payload,
                )
            ]
            for action in screen.extra_actions
        ]
        back = await token_button(
            session,
            services.owner_id,
            "↩️ Back",
            screen.back_action,
            screen.back_payload,
        )
        rows.append([back])
        await session.commit()
    await edit_registered_message(
        message,
        services,
        message_id,
        _screen_text(screen, notice),
        kind=MessageKind.EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=screen.related_id,
    )


async def rerender_text_input(
    message: Message, services: Services, state: dict[str, Any], notice: str
) -> None:
    """Keep the same editor live after a rejected value."""

    await render_text_input(
        message,
        services,
        screen=_screen_from_state(state),
        state=state,
        notice=notice,
    )


async def reject_text_input(
    message: Message, services: Services, state: dict[str, Any], notice: str
) -> None:
    """Hide failed operational input and redraw the same editor with its error."""

    await delete_text_input(message, services)
    await rerender_text_input(message, services, state, notice)


async def handle_text_input(
    message: Message, services: Services, state: dict[str, Any]
) -> bool:
    """Take one typed value into the editor that asked for it.

    The editor keeps itself alive on a refusal, and the typed message leaves the chat only
    once the value is written: it is operational input, not something the owner said.
    """
    flow = services.text_inputs.get(str(state.get("flow", "")))
    if flow is None:
        return False
    try:
        value = validate_text_input(message.text or "", flow.validator(state))
        async with services.sessions() as session:
            # The editor's own session ends here whatever the flow writes; a draft
            # that keeps editing opens a fresh one in its place.
            await session.execute(
                delete(UiSession).where(UiSession.owner_id == services.owner_id)
            )
            await flow.apply(session, services, state, value)
            await session.commit()
    except (DomainError, ValueError) as error:
        await reject_text_input(message, services, state, str(error))
        return True
    await delete_text_input(message, services)
    await flow.render(message, services, state, value)
    return True

