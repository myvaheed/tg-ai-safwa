"""What the owner typing one value into a Card editor writes, and what is redrawn after."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.telegram import TextValidator, required_text
from tg_agent_shell.telegram.contributions import TextInputFlow
from tg_agent_shell.telegram.model import UiSession

from ..use_cases import edit_card_text, update_card_fields
from .creation import render_card_creation
from .draft import sanitize_card_creation_state
from .screens import render_card

_BLOCKED_FLOW = "card_blocked"
# What the editor added to the draft state, and what the draft must not carry back.
_DRAFT_EDITOR_KEYS = frozenset({"text_input", "input_field", "flow"})
CARD_DRAFT_TTL = timedelta(minutes=30)


def _card_text_validator(field: str) -> TextValidator[str] | None:
    """Only the two fields a Card cannot be left without are required."""
    if field == "title":
        return required_text("Card title")
    if field == "blocked_description":
        return required_text("Blocked description")
    return None


async def _apply_card_draft_text(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: str
) -> None:
    """A Card being created is not saved yet, so the value goes back into the draft."""
    draft = {key: item for key, item in state.items() if key not in _DRAFT_EDITOR_KEYS}
    draft[str(state["input_field"])] = value
    session.add(
        UiSession(
            owner_id=services.owner_id,
            kind="card_create",
            state=sanitize_card_creation_state(draft),
            expires_at=datetime.now(UTC) + CARD_DRAFT_TTL,
        )
    )


async def _render_card_draft(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del value
    await render_card_creation(
        message, services, replace_message_id=int(state["text_input"]["message_id"])
    )


def _saved_card_field(state: Mapping[str, Any]) -> str:
    return "blocked_description" if state["flow"] == _BLOCKED_FLOW else str(state["field"])


async def _apply_card_text(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del services
    card_id = int(state["card_id"])
    if state["flow"] == _BLOCKED_FLOW:
        await update_card_fields(
            session, card_id, {"blocked": True, "blocked_description": value}
        )
    else:
        await edit_card_text(session, card_id, str(state["field"]), value)


async def _render_card(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del value
    await render_card(
        message,
        services,
        int(state["card_id"]),
        replace_message_id=int(state["text_input"]["message_id"]),
        back=dict(state.get("back", {})),
    )


CARD_TEXT_INPUTS = (
    TextInputFlow(
        name="card_create",
        validator=lambda state: _card_text_validator(str(state["input_field"])),
        apply=_apply_card_draft_text,
        render=_render_card_draft,
    ),
    TextInputFlow(
        name="card",
        validator=lambda state: _card_text_validator(_saved_card_field(state)),
        apply=_apply_card_text,
        render=_render_card,
    ),
    TextInputFlow(
        name=_BLOCKED_FLOW,
        validator=lambda state: _card_text_validator(_saved_card_field(state)),
        apply=_apply_card_text,
        render=_render_card,
    ),
)
