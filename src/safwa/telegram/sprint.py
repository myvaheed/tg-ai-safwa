"""The Sprint screens: Today, the Sprint dashboard, and the start flow.

Today exists only while a Sprint runs, so both dashboards are here rather than beside the
generic Backlog list.  Starting a Sprint is three screens — Success criteria, then the plan
itself, then Start — because a Sprint that begins without either is a Sprint nobody can
close against anything.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select

from ..domain import DomainError, sprint_length_days, sprint_metrics
from ..enums import CardKind, CardStage, MessageKind
from ..models import Card, Sprint, UiSession, UserProfile, Workspace
from ._core import Services
from ._messaging import paging_row, send_registered, token_button
from ._presentation import menu_row, with_notice
from .cards import card_list_rows, card_list_text

_PROMPT_TTL = timedelta(minutes=30)

TODAY_BACK = {"kind": "dashboard", "stage": CardStage.TODAY.value, "title": "Today", "mode": "today"}
SPRINT_BACK = {
    "kind": "dashboard",
    "stage": CardStage.SPRINT.value,
    "title": "Sprint",
    "mode": "sprint",
}


async def render_today(
    message: Message, services: Services, *, page: int = 0, notice: str | None = None
) -> None:
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        if workspace is None or not workspace.active_sprint_id:
            await send_registered(
                message,
                services,
                "Today opens once a Sprint is running. Plan the next Sprint first.",
                kind=MessageKind.ERROR,
                markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
            )
            return
        cards = await _stage_actions(session, CardStage.TODAY)
        current, descriptions, rows = await card_list_rows(
            session,
            services,
            cards,
            page=page,
            back=TODAY_BACK,
            quick_move=CardStage.SPRINT,
        )
        rows.extend(
            await paging_row(
                session, services.owner_id, current, "dashboard_page", dict(TODAY_BACK)
            )
        )
        rows.append(menu_row())
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(
            card_list_text(
                "Today", current, descriptions, header="🏃 moves an Action back to the Sprint."
            ),
            notice,
        ),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def render_sprint(
    message: Message, services: Services, *, page: int = 0, notice: str | None = None
) -> None:
    """The running Sprint with its Actions, or the Planning screen that starts one."""
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        active_sprint_id = workspace.active_sprint_id if workspace else None
    if active_sprint_id is None:
        await _render_planning(message, services, notice=notice)
        return
    async with services.sessions() as session:
        sprint = await session.get(Sprint, active_sprint_id)
        metrics = await sprint_metrics(session, sprint.id)
        cards = await _stage_actions(session, CardStage.SPRINT)
        current, descriptions, rows = await card_list_rows(
            session,
            services,
            cards,
            page=page,
            back=SPRINT_BACK,
            quick_move=CardStage.TODAY,
        )
        rows.extend(
            await paging_row(
                session, services.owner_id, current, "dashboard_page", dict(SPRINT_BACK)
            )
        )
        rows.append(
            [await token_button(session, services.owner_id, "⏹ Finish early", "sprint_finish")]
        )
        rows.append(menu_row())
        header = (
            f"{sprint.planned_start_date} – {sprint.planned_end_date}\n"
            f"Success criteria: {html.escape(sprint.success_criteria)}\n"
            f"Committed {metrics['committed']} · Added {metrics['added']} · "
            f"Done {metrics['completed']} · Cancelled {metrics['cancelled']}\n"
            "☀️ moves an Action into Today."
        )
        title = f"Sprint {sprint.number}"
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(card_list_text(title, current, descriptions, header=header), notice),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def render_sprint_criteria_prompt(
    message: Message, services: Services, *, notice: str | None = None
) -> None:
    """Ask what the next Sprint must achieve before showing its plan."""
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        if workspace is None or workspace.active_sprint_id:
            raise DomainError("A Sprint is already running")
        current = workspace.sprint_success_criteria.strip()
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="sprint_criteria",
                state={"message_id": message.message_id},
                expires_at=datetime.now(UTC) + _PROMPT_TTL,
            )
        )
        rows: list[list[InlineKeyboardButton]] = []
        if current:
            rows.append(
                [
                    await token_button(
                        session, services.owner_id, "✅ Leave current", "sprint_confirm"
                    )
                ]
            )
        rows.append([await token_button(session, services.owner_id, "↩️ Back", "sprint_back")])
        await session.commit()
    body = (
        f"<b>Current Success criteria</b>\n{html.escape(current)}\n\n"
        if current
        else "<b>Success criteria</b>\n"
    )
    await send_registered(
        message,
        services,
        with_notice(
            body + "Send what this Sprint must achieve. It is what the Sprint is judged against.",
            notice,
        ),
        kind=MessageKind.CARD_EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        replace=False,
    )


async def render_sprint_confirm(
    message: Message, services: Services, *, page: int = 0, notice: str | None = None
) -> None:
    """The plan exactly as it will be committed, plus the one button that commits it."""
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        if workspace is None or workspace.active_sprint_id:
            raise DomainError("A Sprint is already running")
        criteria = workspace.sprint_success_criteria.strip()
    if not criteria:
        await render_sprint_criteria_prompt(
            message, services, notice="Set the Success criteria before starting."
        )
        return
    async with services.sessions() as session:
        length = await sprint_length_days(session)
        profile = await session.get(UserProfile, 1)
        cards = await _stage_actions(session, CardStage.SPRINT, CardStage.TODAY)
        back = {**SPRINT_BACK, "mode": "sprint_confirm"}
        current, descriptions, rows = await card_list_rows(
            session,
            services,
            cards,
            page=page,
            back=back,
            prefix=lambda card: (
                "☀️ " if card.effective_stage == CardStage.TODAY.value else ""
            ),
        )
        rows.extend(await paging_row(session, services.owner_id, current, "dashboard_page", back))
        if cards:
            rows.append(
                [
                    await token_button(
                        session, services.owner_id, "✅ Confirm plan: Start", "sprint_start"
                    )
                ]
            )
        rows.append([await token_button(session, services.owner_id, "↩️ Back", "sprint_back")])
        selected_effort = sum(card.effort_points or 0 for card in cards)
        capacity = profile.capacity_effort_points if profile else None
        start_date = datetime.now(UTC).date()
        header = (
            f"{start_date} – {start_date + timedelta(days=length - 1)} · {length} days\n"
            f"Success criteria: {html.escape(criteria)}\n"
            f"Selected effort: {selected_effort} EP"
            + (
                f"\n⚠️ Above configured capacity ({capacity} EP)."
                if capacity and selected_effort > capacity
                else ""
            )
        )
        await session.commit()
    if not cards:
        header += (
            "\nNothing is planned yet. Move Actions to the Sprint stage first, then start it."
        )
    await send_registered(
        message,
        services,
        with_notice(card_list_text("Sprint plan", current, descriptions, header=header), notice),
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _render_planning(
    message: Message, services: Services, *, notice: str | None = None
) -> None:
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        profile = await session.get(UserProfile, 1)
        length = await sprint_length_days(session)
        selected_effort = (
            await session.scalar(
                select(func.coalesce(func.sum(Card.effort_points), 0)).where(
                    Card.kind == CardKind.ACTION.value,
                    Card.archived_at.is_(None),
                    Card.effective_stage.in_(
                        [CardStage.SPRINT.value, CardStage.TODAY.value]
                    ),
                )
            )
            or 0
        )
        start = await token_button(
            session, services.owner_id, f"▶️ Start {length}-day Sprint", "sprint_criteria_prompt"
        )
        await session.commit()
    criteria = (workspace.sprint_success_criteria or "").strip() if workspace else ""
    capacity = profile.capacity_effort_points if profile else None
    warning = (
        f"\n⚠️ Above configured capacity ({capacity} EP)."
        if capacity and selected_effort > capacity
        else ""
    )
    await send_registered(
        message,
        services,
        with_notice(
            "<b>Planning</b>\nActions in Sprint and Today are preselected for the next Sprint.\n"
            f"Success criteria: {html.escape(criteria) if criteria else 'not set yet'}\n"
            f"Selected effort: {selected_effort} EP{warning}",
            notice,
        ),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[start], menu_row()]),
    )


async def _stage_actions(session, *stages: CardStage) -> list[Card]:  # type: ignore[no-untyped-def]
    return list(
        await session.scalars(
            select(Card).where(
                Card.effective_stage.in_([stage.value for stage in stages]),
                Card.kind == CardKind.ACTION.value,
                Card.archived_at.is_(None),
            )
        )
    )
