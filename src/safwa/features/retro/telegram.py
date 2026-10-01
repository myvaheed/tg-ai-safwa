"""The retro screens: every Sprint that ended, and one of them, read after it closed.

The list is the menu's Retro, newest first. One Sprint's retro is what the list and a
`retro:` citation open: what the Sprint added up to, as written down when it ended, with
the owner's word on whether its Success criteria were met. From it the owner starts the
analysis — one run, watched on one progress message — and reads what the last run made of
the Sprint on a screen of its own. Opened from the list, both lead back to the page it was
opened from.
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
    paginate,
    paging_row,
    send_registered,
    token_button,
)

from ...foundation.workspace import Workspace
from ..cards.api import effort_label, minutes_label
from ..planning.closing import Bucket, RetroStatistics
from ..planning.model import Sprint
from ..profile.api import effort_tracking_on
from .records import ended_sprints
from .use_cases import analysis_input, mark_criterion, record_analysis, require_ended_sprint

logger = logging.getLogger(__name__)

# How many ended Sprints one page of the Retro list holds.
RETRO_LIST_PAGE_SIZE = 10

_MET = {None: "not marked yet", True: "yes", False: "no"}
_MARK = {None: "—", True: "✅", False: "❌"}
_THEN = {None: "not marked", True: "met", False: "not met"}
_TREND = {"up": "▲", "down": "▼", "flat": "●", "unclear": "◌"}


def retro_text(sprint: Sprint, statistics: RetroStatistics, *, effort_tracking: bool = False) -> str:
    lines = [
        f"<b>Sprint {sprint.number} retro</b>",
        f"{sprint.planned_start_date} – {sprint.planned_end_date}",
        f"Success criteria: {html.escape(sprint.success_criteria)}",
        f"Met: {_MET[sprint.criterion_met]}",
        "",
    ]
    if effort_tracking:
        share = f" ({statistics.done_share}%)" if not statistics.unestimated else ""
        lines.extend([
            "<b>Effort</b>",
            f"Taken {effort_label(statistics.taken)} EP, finished "
            f"{effort_label(statistics.done)} EP{share}",
            f"Initial plan {effort_label(statistics.initial)} EP, added "
            f"{effort_label(statistics.added)} EP, taken out {effort_label(statistics.removed)} EP",
        ])
        if statistics.unestimated:
            lines.append(f"{statistics.unestimated} Actions have no estimate. These EP totals are partial.")
        lines.append("")
    lines.extend([
        "<b>Actions</b>",
        f"Taken {statistics.planned} Actions",
        f"Finished {statistics.finished}, remaining {statistics.remaining}, "
        f"of them blocked {statistics.blocked}",
        "",
        *(time_lines(statistics, effort_tracking=effort_tracking and not statistics.unestimated)
          if statistics.time_tracking else ()),
        "<b>Checks on a Value</b>",
    ])
    if statistics.series:
        lines.extend(
            f"{html.escape(tally.title)} ({html.escape(', '.join(tally.values))}): "
            f"Passed {tally.passed}, Missed {tally.missed}"
            for tally in statistics.series
        )
    else:
        lines.append("None was answered while the Sprint ran.")
    return "\n".join(lines)


def _per_hour(effort: float, minutes: int) -> str:
    return f"{effort / (minutes / 60):.1f}"


def _bucket_lines(kind: str, buckets: dict[str, Bucket], *, effort_tracking: bool) -> list[str]:
    """Each Category or Energy type with a time, the most time first."""
    timed = sorted(
        ((name, bucket) for name, bucket in buckets.items() if bucket.timed_count),
        key=lambda pair: -pair[1].minutes,
    )
    whole = sum(bucket.minutes for _, bucket in timed)
    return [f"By {kind}: time · share · per Action" + (" · EP an hour" if effort_tracking else "")] + [
        f"{name} {minutes_label(bucket.minutes)} · {round(100 * bucket.minutes / whole)}% · "
        f"{minutes_label(round(bucket.minutes / bucket.timed_count))}"
        + (f" · {_per_hour(bucket.timed_effort, bucket.minutes)}" if effort_tracking else "")
        for name, bucket in timed
    ]


def time_lines(statistics: RetroStatistics, *, effort_tracking: bool = False) -> list[str]:
    """The Time section of a Sprint closed with Time tracking on; every average is over the
    finished Actions that carry a time."""
    lines = ["<b>Time</b>"]
    if not statistics.timed:
        lines += [f"No time on any of the {statistics.finished} finished Actions.", ""]
        return lines
    tracked = (
        f"Tracked {minutes_label(statistics.minutes)}, "
        f"{minutes_label(round(statistics.minutes / len(statistics.days)))} a day"
    )
    if statistics.day_share is not None:
        tracked += (
            f" — {statistics.day_share}% of a "
            f"{minutes_label(statistics.active_day_minutes)} active day"
        )
    lines += [
        tracked,
        (f"{_per_hour(statistics.timed_effort, statistics.minutes)} EP an hour; recorded on "
         if effort_tracking else "Time recorded on ")
        + f"{statistics.timed} of {statistics.finished} finished Actions "
        f"({round(100 * statistics.timed / statistics.finished)}%)",
        *_bucket_lines("Category", statistics.by_category, effort_tracking=effort_tracking),
        *_bucket_lines("Energy type", statistics.by_energy, effort_tracking=effort_tracking),
        "Longest: "
        + ", ".join(
            f"«{html.escape(action.title)}» {minutes_label(action.minutes)}"
            for action in statistics.longest
        ),
        "",
    ]
    return lines


def _list_label(sprint: Sprint) -> str:
    """One ended Sprint in the list: its number, its dates, the mark, and 🔎 once analysed."""
    return (
        f"{sprint.number} · {sprint.planned_start_date:%d.%m}–{sprint.planned_end_date:%d.%m} "
        f"· {_MARK[sprint.criterion_met]}" + (" 🔎" if sprint.analysis else "")
    )


async def render_retro_list(message: Message, services: Services, *, page: int = 0) -> None:
    """Every Sprint that ended, newest first, a page at a time. The running one has no
    retro yet, so it is not here."""
    async with services.sessions() as session:
        window = paginate(await ended_sprints(session), page, RETRO_LIST_PAGE_SIZE)
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    _list_label(sprint),
                    "retro_open",
                    {"id": sprint.id, "page": window.index},
                )
            ]
            for sprint in window.items
        ]
        rows.extend(await paging_row(session, services.owner_id, window, "retro_list", {}))
        await session.commit()
    text = (
        f"<b>Retro</b> · {window.label}\nEvery Sprint that ended, the newest first."
        if window.items
        else "<b>Retro</b>\nNo Sprint has ended yet. A retro is written when a Sprint ends."
    )
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


async def _back_rows(
    session: AsyncSession, services: Services, page: int | None
) -> list[list[InlineKeyboardButton]]:
    """The way back to the list page the screen was opened from, then the menu."""
    rows = []
    if page is not None:
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "↩️ Back", "retro_list", {"page": page}
                )
            ]
        )
    return [*rows, menu_row()]


async def _retro_buttons(
    session: AsyncSession, services: Services, sprint: Sprint, page: int | None
) -> list[list[InlineKeyboardButton]]:
    """The owner's word on the criteria — the two states it is not in — and the analysis."""
    marks = [("✅ Met", True), ("❌ Not met", False), ("❓ Unmark", None)]
    rows = [
        [
            await token_button(
                session,
                services.owner_id,
                text,
                "retro_mark",
                {"id": sprint.id, "met": met, "page": page},
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
            {"id": sprint.id, "page": page},
        )
    ]
    if sprint.analysis:
        analysis.append(
            await token_button(
                session,
                services.owner_id,
                "📊 Analysis",
                "retro_analysis",
                {"id": sprint.id, "page": page},
            )
        )
    rows.append(analysis)
    return rows + await _back_rows(session, services, page)


async def render_retro(
    message: Message,
    services: Services,
    sprint_id: int,
    *,
    buttons: bool = True,
    page: int | None = None,
) -> None:
    """The retro screen; while the analysis runs it stands without its buttons, so there
    is nothing on it to tap. `page` is the list page it was opened from, if it was."""
    async with services.sessions() as session:
        sprint = await require_ended_sprint(session, sprint_id)
        text = retro_text(
            sprint, RetroStatistics.from_record(sprint.retro),
            effort_tracking=await effort_tracking_on(session),
        )
        rows = await _retro_buttons(session, services, sprint, page) if buttons else []
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


async def render_analysis(
    message: Message, services: Services, sprint_id: int, *, page: int | None = None
) -> None:
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
                    {"id": sprint.id, "page": page},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "📊 Retro",
                    "retro_open",
                    {"id": sprint.id, "page": page},
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


async def _on_list(context: CallbackContext) -> None:
    await render_retro_list(
        context.message, context.services, page=int(context.payload.get("page", 0))
    )


async def _on_open(context: CallbackContext) -> None:
    await render_retro(
        context.message,
        context.services,
        int(context.payload["id"]),
        page=context.payload.get("page"),
    )


async def _on_analysis(context: CallbackContext) -> None:
    await render_analysis(
        context.message,
        context.services,
        int(context.payload["id"]),
        page=context.payload.get("page"),
    )


async def _on_mark(context: CallbackContext) -> None:
    sprint_id = int(context.payload["id"])
    async with context.sessions() as session:
        await mark_criterion(session, sprint_id, context.payload.get("met"))
        await session.commit()
    await render_retro(
        context.message, context.services, sprint_id, page=context.payload.get("page")
    )


async def _on_analyse(context: CallbackContext) -> None:
    """One run under the background lease. While it goes the retro screen stands without
    its buttons, so there is nothing to tap twice; the owner's next message or tap ends
    it, and nothing of a run that did not reach its last call is kept. A run ended that
    way leaves the screen as it stands: the owner's message takes the screen down anyway."""
    sprint_id = int(context.payload["id"])
    page = context.payload.get("page")
    services = context.services
    async with context.sessions() as session:
        number = (await require_ended_sprint(session, sprint_id)).number
    progress = Progress(context.message, services, f"🔎 Analysing Sprint {number}")

    async def run(still_current: Callable[[], bool]) -> None:
        try:
            await render_retro(context.message, services, sprint_id, buttons=False, page=page)
            async with context.sessions() as session:
                given = await analysis_input(session, sprint_id)
            record = await services.features.analyst.analyse(given, report=progress.report)
            # Written and drawn under the lease, and only while it is still this run's.
            if not still_current():
                return
            async with context.sessions() as session:
                await record_analysis(session, sprint_id, record)
                await session.commit()
            await render_analysis(context.message, services, sprint_id, page=page)
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
        await render_retro(context.message, services, sprint_id, page=page)


RETRO_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "retro_list": _on_list,
    "retro_open": _on_open,
    "retro_mark": _on_mark,
    "retro_analyse": _on_analyse,
    "retro_analysis": _on_analysis,
}
