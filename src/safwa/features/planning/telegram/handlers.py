"""Every button the Sprint and the plan screens draw."""

from __future__ import annotations

from sqlalchemy import delete

from tg_agent_shell.telegram import CallbackContext, CallbackHandler, sync_bot_commands
from tg_agent_shell.telegram.model import UiSession

from ....foundation.workspace import Workspace
from ..use_cases import finish_sprint, start_sprint
from .plan import (
    on_plan_card,
    on_plan_filter_toggle,
    on_plan_filters,
    on_plan_move,
    on_plan_open,
    on_plan_page,
)
from .sprint import render_sprint, render_sprint_criteria_prompt


async def _clear_ui(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()


async def _on_sprint_page(context: CallbackContext) -> None:
    await render_sprint(
        context.message,
        context.services,
        page=int(context.payload.get("page", 0)),
        notice=context.payload.get("notice"),
    )


async def _on_criteria_prompt(context: CallbackContext) -> None:
    await render_sprint_criteria_prompt(context.message, context.services)


async def _on_back(context: CallbackContext) -> None:
    await _clear_ui(context)
    await render_sprint(context.message, context.services)


async def _on_start(context: CallbackContext) -> None:
    async with context.sessions() as session:
        workspace = await session.get(Workspace, 1)
        sprint = await start_sprint(
            session,
            success_criteria=workspace.sprint_success_criteria if workspace else "",
        )
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
        number, end_date = sprint.number, sprint.planned_end_date
    await sync_bot_commands(
        context.message.bot, context.services.commands, sprint_active=True
    )
    await render_sprint(
        context.message,
        context.services,
        notice=f"Sprint {number} started. It ends on {end_date}.",
    )


async def _on_finish(context: CallbackContext) -> None:
    async with context.sessions() as session:
        sprint = await finish_sprint(session, reason="finished_early")
        await session.commit()
        number = sprint.number
    await sync_bot_commands(
        context.message.bot, context.services.commands, sprint_active=False
    )
    await render_sprint(
        context.message, context.services, notice=f"Sprint {number} finished early."
    )


PLANNING_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "sprint_page": _on_sprint_page,
    "sprint_criteria_prompt": _on_criteria_prompt,
    "sprint_back": _on_back,
    "sprint_start": _on_start,
    "sprint_finish": _on_finish,
    "plan_open": on_plan_open,
    "plan_page": on_plan_page,
    "plan_move": on_plan_move,
    "plan_card": on_plan_card,
    "plan_filters": on_plan_filters,
    "plan_filter_toggle": on_plan_filter_toggle,
}
