"""How a Sprint reads to the owner: its retro, and the criteria it is planned against."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.screens import TextInputFlow
from ...models import Sprint
from ...telegram.sprint import render_sprint, render_sprint_retro
from ...telegram.text_input import required_text
from .use_cases import set_sprint_success_criteria


async def retro_citation_label(session: AsyncSession, services: Any, sprint: Sprint) -> str:
    return f"📊 Sprint {sprint.number} retro"


async def open_sprint_retro(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    """The retro is always its own message: it is what a finished Sprint left behind."""
    await render_sprint_retro(message, services, item_id)


async def _apply_success_criteria(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del services, state
    await set_sprint_success_criteria(session, value)


async def _render_sprint(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del value
    await render_sprint(
        message, services, replace_message_id=int(state["text_input"]["message_id"])
    )


TEXT_INPUT = TextInputFlow(
    name="sprint",
    validator=lambda _state: required_text("Success criteria"),
    apply=_apply_success_criteria,
    render=_render_sprint,
)
