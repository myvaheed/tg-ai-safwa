"""The Sprint planning screen: the Sprint is a table, the Backlog is the keyboard.

The message carries what is already planned — one row per Action, its title a link that
opens the Card in place of the screen, its effort points beside it, and a `↩️ Return` link
that sends it back to the Backlog. The keyboard under it is the Backlog: one page of
Actions, each with `📥`, narrowed to the Cards every picked Request returns.

The page and the picked Requests are the screen's own Place, so every button and link on
it carries them, and a Card opened from it comes back to the same page and filters.
"""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    Page,
    Place,
    Services,
    back_button,
    edit_registered_message,
    go,
    paginate,
    place_button,
    place_link,
    send_registered,
)

from ...cards.api import CardStage, actions_on_stages, effort_label
from ...cards.model import Card
from ...cards.use_cases import move_card
from ...profile.api import effort_tracking_on
from ...saved_requests.model import SavedRequest
from ..api import PlanLoad, capacity_effort_points, plan_load
from .sprint import SPRINT_SCREEN, plan_cost
from .state import resolve_filters

# The Sprint plan puts both columns in one table, so a row is one Card on each side.
SPRINT_PLAN_PAGE_SIZE = 10
SPRINT_PLAN_TITLE_LIMIT = 60

_RETURN = "↩️ Return"


async def _table(
    session: AsyncSession,
    services: Services,
    here: Place,
    planned: list[Card],
    load: PlanLoad,
    *,
    effort_tracking: bool = False,
) -> str:
    rows = [
        '<tr><th align="left">Planned in Sprint</th>'
        + ('<th align="right">EP</th>' if effort_tracking else '')
        + '<th align="center"></th></tr>'
    ]
    if not planned:
        rows.append(f'<tr><td colspan="{3 if effort_tracking else 2}" align="center">Nothing planned yet.</td></tr>')
    for card in planned:
        title = await place_link(
            session, services, html.escape(card.title), here.child("card_view", id=card.id)
        )
        count = load.counts[card.id]
        if count != 1:
            title += f" × {count if count is not None else '?'}"
        effort = effort_label((card.effort_points or 0) * count) if count is not None and card.effort_points is not None else "?"
        back = await place_link(
            session, services, _RETURN, Place("plan_return", {"id": card.id}, here)
        )
        rows.append(
            f'<tr><td align="left">{title}</td>'
            + (f'<td align="right">{effort}</td>' if effort_tracking else '')
            + f'<td align="center">{back}</td></tr>'
        )
    return "<table bordered striped>" + "".join(rows) + "</table>"


def _button_label(card: Card, *, effort_tracking: bool = False) -> str:
    """Keep a long title readable in a full-width keyboard row."""
    title = card.title
    if len(title) > SPRINT_PLAN_TITLE_LIMIT:
        title = f"{title[: SPRINT_PLAN_TITLE_LIMIT - 1]}…"
    return f"{title} ({effort_label(card.effort_points)})" if effort_tracking else title


async def _markup(
    session: AsyncSession,
    services: Services,
    here: Place,
    shown: Page,
    *,
    backlog_total: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    effort_tracking = await effort_tracking_on(session)
    filters = list(here.args["filters"])
    for card in shown.items:
        rows.append(
            [
                await place_button(
                    session,
                    services.owner_id,
                    _button_label(card, effort_tracking=effort_tracking),
                    Place("plan_move", {"id": card.id}, here),
                ),
            ]
        )
    if filters and not shown.items:
        # The keyboard is empty because the filter says so, not because the Backlog is.
        rows.append(
            [
                await place_button(
                    session, services.owner_id, f"0 of {backlog_total} Actions match", here
                )
            ]
        )
    rows.append(
        [
            await place_button(
                session,
                services.owner_id,
                f"🔎 Apply filter ({len(filters)})" if filters else "🔎 Apply filter",
                here.child("plan_filters", filters=filters),
            )
        ]
    )

    async def step(offset: int, glyph: str) -> InlineKeyboardButton:
        index = min(max(shown.index + offset, 0), shown.count - 1)
        return await place_button(session, services.owner_id, glyph, here.but(page=index))

    rows.append(
        [
            await step(-1, "⬅️"),
            await place_button(
                session, services.owner_id, f"{shown.index + 1}/{shown.count}", here
            ),
            await step(1, "➡️"),
        ]
    )
    rows.append([await back_button(session, services.owner_id, here.back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_plan(
    message: Message,
    services: Services,
    *,
    page: int = 0,
    filters: list[int] | tuple[int, ...] = (),
    replace_message_id: int | None = None,
) -> None:
    """Draw the plan on `page`, narrowed to the Cards every Request in `filters` returns."""
    async with services.sessions() as session:
        live, matched = await resolve_filters(session, list(filters), services.views)
        planned = await actions_on_stages(session, CardStage.SPRINT, CardStage.TODAY)
        backlog = await actions_on_stages(session, CardStage.BACKLOG)
        planned.sort(key=lambda card: card.id)
        backlog.sort(key=lambda card: card.id)
        selectable = backlog if matched is None else [c for c in backlog if c.id in matched]
        shown = paginate(selectable, page, SPRINT_PLAN_PAGE_SIZE)
        here = SPRINT_SCREEN.child("plan_page", page=shown.index, filters=live)
        markup = await _markup(session, services, here, shown, backlog_total=len(backlog))
        effort_tracking = await effort_tracking_on(session)
        load = await plan_load(session, planned)
        cost, warning = plan_cost(
            load, await capacity_effort_points(session), effort_tracking=effort_tracking
        )
        table = await _table(
            session, services, here, planned, load, effort_tracking=effort_tracking
        )
        await session.commit()
    body = (
        "<p><b>Sprint plan</b></p>"
        f"<p>In Sprint: {cost}</p>"
        + (f"<p>{warning}</p>" if warning else "")
        + table
    )
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            body,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            rich=True,
        )
    else:
        await send_registered(
            message, services, body, kind=MessageKind.DASHBOARD, markup=markup, rich=True
        )


async def render_plan_filters(
    message: Message, services: Services, filters: list[int], *, back: Place
) -> None:
    """Pick the saved Requests that narrow the Backlog. Every picked one has to match.
    `back` is the plan, and it is drawn with what was picked here."""
    async with services.sessions() as session:
        picked = set(filters)
        requests = list(
            await session.scalars(
                select(SavedRequest).order_by(SavedRequest.name)
            )
        )
        rows = [
            [
                await place_button(
                    session,
                    services.owner_id,
                    f"{'☑' if request.id in picked else '☐'} {request.name}"[:60],
                    Place("plan_filter_toggle", {"id": request.id, "filters": filters}, back),
                )
            ]
            for request in requests
        ]
        rows.append([await back_button(session, services.owner_id, back)])
        await session.commit()
    text = "<b>Filters</b>\n" + (
        "A Backlog Action is shown only if every picked Request returns it."
        if requests
        else "Your advisor has not saved any Requests yet."
    )
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def on_plan_page(context: CallbackContext) -> None:
    await render_plan(
        context.message,
        context.services,
        page=int(context.payload.get("page", 0)),
        filters=list(context.payload.get("filters", [])),
    )


async def on_plan_move(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await move_card(session, int(context.payload["id"]), CardStage.SPRINT)
        await session.commit()
    await go(context, context.back)


async def on_plan_return(context: CallbackContext) -> None:
    """A planned Action's `↩️ Return`: back to the Backlog, and the plan drawn again."""
    async with context.sessions() as session:
        card = await session.get(Card, int(context.payload["id"]))
        if card is not None:
            await move_card(session, card.id, CardStage.BACKLOG)
        await session.commit()
    await go(context, context.back)


async def on_plan_filters(context: CallbackContext) -> None:
    await render_plan_filters(
        context.message, context.services, list(context.payload["filters"]), back=context.back
    )


async def on_plan_filter_toggle(context: CallbackContext) -> None:
    request_id = int(context.payload["id"])
    picked = list(context.payload["filters"])
    if request_id in picked:
        picked.remove(request_id)
    else:
        picked.append(request_id)
    # A different Backlog is a different page count, so the page cannot survive.
    await render_plan_filters(
        context.message,
        context.services,
        picked,
        back=context.back.but(filters=picked, page=0),
    )
