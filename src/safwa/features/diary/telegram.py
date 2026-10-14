"""The Diary browser, a saved day and a proposed day."""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.media.telegram import send_photo_screen
from tg_agent_shell.proposals.api import (
    ChangeAction,
    ProposalChange,
    ProposalScreen,
    ShownField,
)
from tg_agent_shell.proposals.marks import shown
from tg_agent_shell.proposals.render import (
    ACTION_VERBS,
    detail_lines,
)
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    back_button,
    dismiss_prior_ui,
    paginate,
    paging_row,
    place_button,
    render_citations,
    send_registered,
)

from ..schedules.api import workspace_zone
from .api import day_media
from .model import DiaryEntry
from .use_cases import diary_entry_for

DIARY_PAGE_SIZE = 10
DIARY_RECENT_DAYS = 7

# A month inside a date, "11 октября".
DIARY_MONTH_NAMES = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
# A month on its own, on its button and its screen.
DIARY_MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
FEELING_SCORE_EMOJI = {
    0: "⚫",
    1: "😨",
    2: "😞",
    3: "🙁",
    4: "😕",
    5: "😐",
    6: "🙂",
    7: "😊",
    8: "😃",
    9: "🤩",
    10: "🌟",
}


def diary_label(entry_date: date, feeling_score: int | None) -> str:
    """How a Diary day is named everywhere: on its screen, and on the link that opens it."""
    written = f"{entry_date.day} {DIARY_MONTH_NAMES[entry_date.month - 1]}"
    emoji = FEELING_SCORE_EMOJI.get(feeling_score) if feeling_score is not None else None
    return f"{written} · {emoji}{feeling_score}" if emoji else written


async def command_diary(message: Message, services: Services) -> None:
    await render_browser(message, services)


async def render_browser(
    message: Message,
    services: Services,
    *,
    year: int | None = None,
    month: int | None = None,
    recent: bool = False,
    page: int = 0,
    back: Place | None = None,
) -> None:
    async with services.sessions() as session:
        query = select(DiaryEntry.entry_date, DiaryEntry.feeling_score)
        if recent:
            today = utcnow().astimezone(await workspace_zone(session)).date()
            first = today - timedelta(days=DIARY_RECENT_DAYS - 1)
            query = query.where(DiaryEntry.entry_date.between(first, today))
        elif year is not None:
            first = date(year, month or 1, 1)
            last = (
                date(year + 1, 1, 1)
                if month is None or month == 12
                else date(year, month + 1, 1)
            )
            query = query.where(DiaryEntry.entry_date >= first, DiaryEntry.entry_date < last)
        scores = dict((await session.execute(query)).all())
        heading = "📔 Diary"
        if recent:
            heading += " · Last 7 days"
            items = [today - timedelta(days=offset) for offset in range(DIARY_RECENT_DAYS)]
        elif month is not None:
            heading += f" · {year} · {DIARY_MONTHS[month - 1]}"
            items = sorted(scores, reverse=True)
        elif year is not None:
            heading += f" · {year}"
            items = sorted({day.month for day in scores}, reverse=True)
        else:
            items = sorted({day.year for day in scores}, reverse=True)
        size = 12 if year is not None and month is None and not recent else DIARY_PAGE_SIZE
        shown = paginate(items, page, size)
        here = Place(
            "diary_browser",
            {"year": year, "month": month, "recent": recent, "page": shown.index},
            back,
        )
        rows: list[list[InlineKeyboardButton]] = []
        for item in shown.items:
            if isinstance(item, date):
                label = f"{diary_label(item, scores.get(item))} · {item.year}"
                target = here.child("diary_day", date=item.isoformat())
            elif year is not None:
                label = DIARY_MONTHS[item - 1]
                target = here.child("diary_browser", year=year, month=item)
            else:
                label = str(item)
                target = here.child("diary_browser", year=item)
            rows.append([await place_button(session, services.owner_id, label, target)])
        if year is None and not recent:
            rows.append([
                await place_button(
                    session, services.owner_id, "Last 7 days", here.child("diary_browser", recent=True)
                )
            ])
        rows.extend(await paging_row(session, services.owner_id, shown, here))
        rows.append([await back_button(session, services.owner_id, back)])
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>{heading}</b> · {shown.label}" + ("" if items else "\nNo entries here yet."),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _on_browser(context: CallbackContext) -> None:
    await dismiss_prior_ui(context.message, context.services)
    await render_browser(
        context.message,
        context.services,
        year=context.payload.get("year"),
        month=context.payload.get("month"),
        recent=bool(context.payload.get("recent", False)),
        page=int(context.payload.get("page", 0)),
        back=context.back,
    )


async def _on_day(context: CallbackContext) -> None:
    day = date.fromisoformat(context.payload["date"])
    async with context.sessions() as session:
        entry = await diary_entry_for(session, day)
        if entry is None:
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                await back_button(session, context.owner_id, context.back)
            ]])
            await session.commit()
    if entry is not None:
        await render_diary(context.message, context.services, entry.id, back=context.back)
    else:
        await send_registered(
            context.message,
            context.services,
            f"<b>📔 Diary · {day:%d.%m.%Y}</b>\nNo entry for this day.",
            kind=MessageKind.DASHBOARD,
            markup=markup,
        )


DIARY_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "diary_browser": _on_browser,
    "diary_day": _on_day,
}


async def render_diary(
    message: Message,
    services: Services,
    entry_id: int,
    *,
    back: Place | None = None,
    replace: bool | None = None,
) -> None:
    """One Diary day, in full and read-only: its photos as one album, its words below.

    The Diary is written through proposals alone.
    """
    async with services.sessions() as session:
        entry = await session.get(DiaryEntry, entry_id)
        if entry is None:
            raise DomainError("Diary entry does not exist")
        label = diary_label(entry.entry_date, entry.feeling_score)
        here = Place("diary_day", {"date": entry.entry_date.isoformat()}, back)
        body = await render_citations(session, services, html.escape(entry.body or ""), here=here)
        media_ids = [media_id for media_id, _label in await day_media(session, entry_id)]
        rows = [[await back_button(session, services.owner_id, back)]]
        await session.commit()
    await send_photo_screen(
        message,
        services,
        media_ids,
        "\n\n".join(
            filter(None, (f"<b>📔 {html.escape(label)}</b>", body))
        ),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=entry_id,
        replace=replace,
    )


def _day_lines(change: ProposalChange) -> list[str]:
    """The day's shape, never its text: a receipt stays in the conversation for good.

    A day printed here would be re-read on every later turn and would spend the history
    budget it costs.  The Diary session holds its own draft, and a saved day is in
    `ai_diary`, so the body never has to travel. A photo is named by its few words.
    """
    values = dict(change.values)
    lines = [f"Date: {values.get('entry_date', '')}"]
    if change.action is ChangeAction.DELETE:
        lines.append("Entry: removed")
        return lines
    if values.get("body") is not None:
        lines.append(f"Entry: {len(str(values['body']))} characters")
    if values.get("feeling_score") is not None:
        lines.append(f"Feeling: {values['feeling_score']}")
    lines += [f"Photo added: {item['meta']}" for item in values.get("add_media") or ()]
    lines += [f"Photo taken off: {item['meta']}" for item in values.get("remove_media") or ()]
    lines += [
        f"Photo renamed: {item['was']} → {item['meta']}"
        for item in values.get("rename_media") or ()
    ]
    return lines


def _feeling(state: dict[str, Any]) -> str | None:
    score = state.get("feeling_score")
    return None if score is None else f"{int(score)} {FEELING_SCORE_EMOJI[int(score)]}"


class DiaryProposalPresenter:
    entity = "diary"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values))

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        return _day_lines(change)

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        values = dict(change.values)
        verb = ACTION_VERBS.get(change.action, change.action.title())
        label = f"{verb} Diary entry for {values.get('entry_date', '')}".strip()
        score = values.get("feeling_score")
        return label if score is None else f"{label} with feeling score {score}"

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        current: dict[str, Any] = {}
        on_day: list[str] = []
        if changes[0].entity_id:
            entry = await session.get(DiaryEntry, changes[0].entity_id)
            if entry is not None:
                current = {"body": entry.body, "feeling_score": entry.feeling_score}
                on_day = [label for _media_id, label in await day_media(session, entry.id)]
        proposed = dict(current)
        added: list[str] = []
        removed: list[str] = []
        renamed: list[dict[str, Any]] = []
        for change in changes:
            values = dict(change.values)
            added += [item["meta"] for item in values.pop("add_media", None) or ()]
            removed += [item["meta"] for item in values.pop("remove_media", None) or ()]
            renamed += values.pop("rename_media", None) or ()
            if values.get("body") is None:
                # No words in the change: the words already saved stay.
                values.pop("body", None)
            proposed.update(values)
        # One day is one entry: whatever the last change does is what Save leaves.
        change = changes[-1]
        day = str(proposed.get("entry_date") or "")
        removing = change.action is ChangeAction.DELETE
        if removing:
            photos = [f"📷 {html.escape(meta)}" for meta in on_day]
        else:
            # A new day reads plain; an edited one marks the photos it adds and takes off.
            new = "{}" if change.action is ChangeAction.CREATE else "<b>{}</b>"
            photos = [f"📷 {new.format(html.escape(meta))}" for meta in added]
            photos += [
                f"📷 <s>{html.escape(item['was'])}</s> <b>{html.escape(item['meta'])}</b>"
                for item in renamed
            ]
            photos += [f"📷 <s>{html.escape(meta)}</s>" for meta in removed]
        blocks = [
            f"<i>{html.escape(str(proposed['remark']))}</i>"
            if proposed.get("remark") and not removing
            else "",
            "\n".join(photos),
            "This removes that day's entry for good." if removing else "",
        ]
        return ProposalScreen(
            item="Diary entry",
            fields=(
                ShownField("Date", day, day, anchor=True),
                ShownField("Feeling", _feeling(current), _feeling(proposed)),
                # The day's words stay on screen: a photo put on a day is put among them.
                ShownField(
                    "Entry", shown(current.get("body")), shown(proposed.get("body")), anchor=True
                ),
            ),
            blocks=tuple(block for block in blocks if block),
        )


async def diary_citation_label(
    session: AsyncSession, services: Any, entry: DiaryEntry, *, short: bool
) -> str:
    return diary_label(entry.entry_date, entry.feeling_score)
