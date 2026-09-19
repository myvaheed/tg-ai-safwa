"""The retro screen: the same Sprint, read after it closed.

It is what a `retro:` citation opens: what the Sprint added up to, as written down when it
ended, with the owner's word on whether its Success criteria were met. From it the owner
starts the analysis — one run, watched on one progress message — and reads what the last
run made of the Sprint on a screen of its own.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Progress,
    Services,
    menu_row,
    send_registered,
    token_button,
)

from ...foundation.workspace import Workspace
from ..cards.api import effort_label
from ..planning.closing import RetroStatistics
from ..planning.model import Sprint
from .use_cases import analysis_input, mark_criterion, record_analysis, require_ended_sprint

logger = logging.getLogger(__name__)

_MET = {None: "not marked yet", True: "yes", False: "no"}
_THEN = {None: "not marked", True: "met", False: "not met"}
_TREND = {"up": "▲", "down": "▼", "flat": "●", "unclear": "◌"}


def retro_text(sprint: Sprint, statistics: RetroStatistics) -> str:
    lines = [
        f"<b>Sprint {sprint.number} retro</b>",
        f"{sprint.planned_start_date} – {sprint.planned_end_date}",
        f"Success criteria: {html.escape(sprint.success_criteria)}",
        f"Met: {_MET[sprint.criterion_met]}",
        "",
        "<b>Effort</b>",
        f"Taken {effort_label(statistics.taken)} EP, finished "
        f"{effort_label(statistics.done)} EP ({statistics.done_share}%)",
        f"Initial plan {effort_label(statistics.initial)} EP, added "
        f"{effort_label(statistics.added)} EP, taken out {effort_label(statistics.removed)} EP",
        "",
        "<b>Actions</b>",
        f"Finished {statistics.finished}, remaining {statistics.remaining}, "
        f"of them blocked {statistics.blocked}",
        "",
        "<b>Checks on a Value</b>",
    ]
    if statistics.series:
        lines.extend(
            f"{html.escape(tally.title)} ({html.escape(', '.join(tally.values))}): "
            f"Passed {tally.passed}, Missed {tally.missed}"
            for tally in statistics.series
        )
    else:
        lines.append("None was answered while the Sprint ran.")
    return "\n".join(lines)


async def _retro_buttons(
    session: AsyncSession, services: Services, sprint: Sprint
) -> list[list[InlineKeyboardButton]]:
    """The owner's word on the criteria — the two states it is not in — and the analysis."""
    marks = [("✅ Met", True), ("❌ Not met", False), ("❓ Unmark", None)]
    rows = [
        [
            await token_button(
                session, services.owner_id, text, "retro_mark", {"id": sprint.id, "met": met}
            )
            for text, met in marks
            if met is not sprint.criterion_met
        ]
    ]
    analysis = [
        await token_button(
            session,
            services.owner_id,
            "🔁 Analyse again" if sprint.analysis else "🔎 Analyse with AI",
            "retro_analyse",
            {"id": sprint.id},
        )
    ]
    if sprint.analysis:
        analysis.append(
            await token_button(
                session, services.owner_id, "📊 Analysis", "retro_analysis", {"id": sprint.id}
            )
        )
    rows.append(analysis)
    rows.append(menu_row())
    return rows


async def render_retro(
    message: Message, services: Services, sprint_id: int, *, buttons: bool = True
) -> None:
    """The retro screen; while the analysis runs it stands without its buttons, so there
    is nothing on it to tap."""
    async with services.sessions() as session:
        sprint = await require_ended_sprint(session, sprint_id)
        text = retro_text(sprint, RetroStatistics.from_record(sprint.retro))
        rows = await _retro_buttons(session, services, sprint) if buttons else []
        await session.commit()
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
    )


async def retro_citation_label(session: AsyncSession, services: Any, sprint: Sprint) -> str:
    return f"📊 Sprint {sprint.number} retro"


async def open_retro(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    """The retro is always its own message: it is what a finished Sprint left behind."""
    await render_retro(message, services, item_id)


# ---------------------------------------------------------------------------- the analysis


def analysis_text(
    sprint: Sprint, statistics: RetroStatistics, analysis: dict[str, Any], tz: ZoneInfo
) -> str:
    """The analysis screen, headed by the days the run read rather than the planned dates,
    and by the owner's mark as the run read it rather than as it stands."""
    compared = ", ".join(analysis.get("compared") or [])
    analysed = datetime.fromisoformat(analysis["analysed_at"]).astimezone(tz)
    lines = [
        f"<b>Sprint {sprint.number} analysis</b>",
        f"{statistics.first_day} – {statistics.last_day}"
        + (f" · against {html.escape(compared)}" if compared else ""),
        f"Analysed {analysed:%Y-%m-%d %H:%M}, the criteria then {_THEN[analysis.get('met')]}",
    ]
    if analysis.get("met") is not sprint.criterion_met:
        lines.append(
            f"Marked {_THEN[sprint.criterion_met]} since; analyse again for the mark to count."
        )
    lines += [
        "",
        f"<i>{html.escape(analysis['headline'])}</i>",
        "",
        "<b>Trends</b>",
        *(
            [
                f"{_TREND.get(finding['trend'], '◌')} {html.escape(finding['metric'])} — "
                f"{html.escape(finding['note'])}"
                for finding in analysis["dynamics"]
            ]
            or ["Nothing to compare yet."]
        ),
    ]
    for title, name in (
        ("What raised the day's rating", "helped"),
        ("What lowered it", "hurt"),
        ("What had nothing to do with it", "noise"),
    ):
        lines += ["", f"<b>{title}</b>"]
        lines += [f"• {html.escape(item)}" for item in analysis[name]] or ["Nothing confirmed."]
    lines += [
        "",
        "<b>Experiment for the next Sprint</b>",
        f"→ {html.escape(analysis['experiment'])}",
    ]
    if analysis.get("notable"):
        lines += ["", "<b>Worth knowing next Sprint</b>"]
        lines += [f"• {html.escape(item)}" for item in analysis["notable"]]
    return "\n".join(lines)


async def render_analysis(message: Message, services: Services, sprint_id: int) -> None:
    async with services.sessions() as session:
        sprint = await require_ended_sprint(session, sprint_id)
        if sprint.analysis is None:
            raise DomainError("This Sprint has not been analysed yet")
        workspace = await session.get(Workspace, 1)
        tz = ZoneInfo(workspace.timezone if workspace else "UTC")
        text = analysis_text(
            sprint, RetroStatistics.from_record(sprint.retro), sprint.analysis, tz
        )
        rows: list[list[InlineKeyboardButton]] = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    "🔁 Analyse again",
                    "retro_analyse",
                    {"id": sprint.id},
                ),
                await token_button(
                    session, services.owner_id, "📊 Retro", "retro_open", {"id": sprint.id}
                ),
            ],
            menu_row(),
        ]
        await session.commit()
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _on_open(context: CallbackContext) -> None:
    await render_retro(context.message, context.services, int(context.payload["id"]))


async def _on_analysis(context: CallbackContext) -> None:
    await render_analysis(context.message, context.services, int(context.payload["id"]))


async def _on_mark(context: CallbackContext) -> None:
    sprint_id = int(context.payload["id"])
    async with context.sessions() as session:
        await mark_criterion(session, sprint_id, context.payload.get("met"))
        await session.commit()
    await render_retro(context.message, context.services, sprint_id)


async def _on_analyse(context: CallbackContext) -> None:
    """One run under the background lease. While it goes the retro screen stands without
    its buttons, so there is nothing to tap twice; the owner's next message or tap ends
    it, and nothing of a run that did not reach its last call is kept. A run ended that
    way leaves the screen as it stands: the owner's message takes the screen down anyway."""
    sprint_id = int(context.payload["id"])
    services = context.services
    async with context.sessions() as session:
        number = (await require_ended_sprint(session, sprint_id)).number
    progress = Progress(context.message, services, f"🔎 Analysing Sprint {number}")

    async def run(still_current: Callable[[], bool]) -> None:
        try:
            await render_retro(context.message, services, sprint_id, buttons=False)
            async with context.sessions() as session:
                given = await analysis_input(session, sprint_id)
            record = await services.features.analyst.analyse(given, report=progress.report)
            # Written and drawn under the lease, and only while it is still this run's.
            if not still_current():
                return
            async with context.sessions() as session:
                await record_analysis(session, sprint_id, record)
                await session.commit()
            await render_analysis(context.message, services, sprint_id)
        finally:
            await progress.clear()

    try:
        await services.turn.run_background(run)
    except Exception as error:
        logger.exception("The analysis of Sprint %s failed", number)
        await send_registered(
            context.message,
            services,
            f"The analysis of Sprint {html.escape(number)} could not be finished.\n"
            f"{html.escape(str(error))}",
            kind=MessageKind.ERROR,
            replace=False,
        )
        await render_retro(context.message, services, sprint_id)


RETRO_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "retro_open": _on_open,
    "retro_mark": _on_mark,
    "retro_analyse": _on_analyse,
    "retro_analysis": _on_analysis,
}
