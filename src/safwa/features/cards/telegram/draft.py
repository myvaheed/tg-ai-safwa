"""What a Card being written by hand holds, and what still stops it being saved.

The draft is a `UiSession` row and nothing else: no Card exists until Save. Kept apart from
the screen that draws it because the selectors read the same state.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.telegram.model import UiSession

from ..model import CardKind, CardStage, Priority
from ..use_cases import validate_action_fields, validate_blocked_fields


def new_card_creation_state() -> dict[str, Any]:
    return {
        "kind": CardKind.ACTION.value,
        "title": "",
        "note": "",
        "stage": CardStage.BACKLOG.value,
        "priority": Priority.MEDIUM.value,
        "hard_time": False,
        "blocked": False,
        "blocked_description": "",
        "effort_points": None,
        "repeatable": False,
        "categories": [],
        "energy_types": [],
        "value_ids": [],
        "tag_ids": [],
    }


def sanitize_card_creation_state(state: dict[str, Any]) -> dict[str, Any]:
    clean = {**new_card_creation_state(), **state}
    try:
        clean["kind"] = CardKind(clean["kind"]).value
    except ValueError:
        clean["kind"] = CardKind.ACTION.value
    try:
        clean["stage"] = CardStage(clean["stage"]).value
    except ValueError:
        clean["stage"] = CardStage.BACKLOG.value
    if clean["stage"] in {CardStage.DONE.value, CardStage.CANCELLED.value}:
        clean["stage"] = CardStage.BACKLOG.value
    if clean["kind"] != CardKind.ACTION.value:
        clean.update(
            stage=CardStage.BACKLOG.value,
            effort_points=None,
            repeatable=False,
            blocked=False,
            categories=[],
            energy_types=[],
        )
    if not clean["blocked"]:
        clean["blocked_description"] = ""
    for field in ("categories", "energy_types", "value_ids", "tag_ids"):
        clean[field] = list(dict.fromkeys(clean.get(field) or []))
    return clean


def card_creation_errors(state: dict[str, Any]) -> list[str]:
    """Report what still blocks Save, using the same rules the domain enforces.

    The draft is checked here only so Save can be hidden until it would succeed;
    ``create_card`` remains the authority and revalidates everything.
    """
    errors: list[str] = []
    if not str(state.get("title", "")).strip():
        errors.append("Add a title")
    for check in (
        lambda: validate_action_fields(
            state["kind"],
            state.get("effort_points"),
            bool(state.get("repeatable")),
            set(state.get("categories") or []),
            set(state.get("energy_types") or []),
            blocked=bool(state.get("blocked")),
        ),
        lambda: validate_blocked_fields(
            bool(state.get("blocked")), state.get("blocked_description")
        ),
    ):
        try:
            check()
        except DomainError as error:
            errors.append(str(error))
    return errors


async def require_card_draft(session: AsyncSession, owner_id: int) -> UiSession:
    draft = await session.scalar(
        select(UiSession).where(
            UiSession.owner_id == owner_id,
            UiSession.kind == "card_create",
        )
    )
    if draft is None:
        raise DomainError("Card creation is no longer active")
    return draft


async def card_editor_back_state(session: AsyncSession, owner_id: int) -> dict[str, Any]:
    """Keep the navigation trail of the Card screen a focused prompt replaces."""
    editor = await session.scalar(select(UiSession).where(UiSession.owner_id == owner_id))
    if editor is None or editor.kind != "card_editor":
        return {}
    return dict(editor.state.get("back", {}))
