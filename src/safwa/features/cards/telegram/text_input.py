"""What the owner typing one value into a Card editor writes, and what is redrawn after."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.telegram import TextValidator, required_text
from tg_agent_shell.telegram.contributions import TextInputFlow
from tg_agent_shell.telegram.model import UiSession

from ..hard_time import hard_time_columns, typed_hard_time
from ..model import TRACKED_MINS_MAX
from ..use_cases import edit_card_text, update_card_fields
from .creation import render_card_creation
from .draft import sanitize_card_creation_state
from .screens import render_card

_BLOCKED_FLOW = "card_blocked"
# What the editor added to the draft state, and what the draft must not carry back.
_DRAFT_EDITOR_KEYS = frozenset({"text_input", "input_field", "flow"})
CARD_DRAFT_TTL = timedelta(minutes=30)
TIME_SPENT_INSTRUCTION = (
    f"Send the time it took, up to {TRACKED_MINS_MAX // 60}h: 331, 5:31 or 5h 31m. "
    "Send off to clear it."
)


def parse_minutes(raw: str) -> int | None:
    """The time typed as minutes, as hours:minutes or as hours and minutes; off is none."""
    text = raw.strip()
    if text.casefold() == "off":
        return None
    minutes: int | None = None
    if text.isdecimal():
        minutes = int(text)
    elif (clock := re.fullmatch(r"(\d+):([0-5]\d)", text)) is not None:
        minutes = int(clock[1]) * 60 + int(clock[2])
    elif text and (
        spoken := re.fullmatch(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", text, re.IGNORECASE)
    ) is not None:
        minutes = int(spoken[1] or 0) * 60 + int(spoken[2] or 0)
    if minutes is None or not 1 <= minutes <= TRACKED_MINS_MAX:
        raise ValueError(TIME_SPENT_INSTRUCTION)
    return minutes


def _card_text_validator(field: str) -> TextValidator[Any] | None:
    """The time is read as minutes; only the two fields a Card cannot be left without are
    required."""
    if field == "tracked_mins":
        return parse_minutes
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
    if state["input_field"] == "hard_time":
        draft["hard_time"] = hard_time_columns(await typed_hard_time(session, value))["hard_time"]
    else:
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
    elif state["field"] == "hard_time":
        await update_card_fields(
            session, card_id, {"hard_time": await typed_hard_time(session, value)}
        )
    elif state["field"] == "tracked_mins":
        await update_card_fields(session, card_id, {"tracked_mins": value})
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
        # Only the full view offers a field to type into, so that is where typing returns.
        full=True,
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
