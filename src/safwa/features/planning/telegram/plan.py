"""The Sprint planning screen: the Sprint is a table, the Backlog is the keyboard.

The message carries what is already planned — one row per Action, its title a link that
opens the Card in place of the screen, its effort points beside it, and a `↩️ Return` link
that sends it back to the Backlog. The keyboard under it is the Backlog: one page of
Actions, each with `📥`, narrowed to the Cards every picked Request returns.

Screen state — the page and the picked Requests — lives in one `UiSession`, because a
deep-link payload is 64 characters of `[A-Za-z0-9_-]` and cannot carry it. Opening a Card
replaces that session with the Card editor's, so the state travels the other way as the
editor's `back`, and `card_back` restores it.
"""

from __future__ import annotations

import html
import re
from collections import deque
from time import monotonic

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....constants import (
    PLAN_LINK_BURST_SECONDS,
    PLAN_LINK_BURST_TAPS,
    SPRINT_PLAN_PAGE_SIZE,
    SPRINT_PLAN_TITLE_LIMIT,
)
from ....enums import MessageKind
from ....models import Card, SavedRequest
from ....shell import (
    CallbackContext,
    Page,
    Services,
    edit_registered_message,
    paginate,
    send_registered,
    send_toast,
    start_payload,
    token_button,
)
from ...cards.api import CardStage, actions_on_stages
from ...cards.telegram import render_card
from ...cards.use_cases import move_card
from ...profile.api import capacity_effort_points
from .sprint import plan_cost
from .state import (
    load_plan_state,
    plan_back,
    plan_screen_id,
    resolve_filters,
    store_state,
)

_RETURN = "↩️ Return"
_INTO_SPRINT = "📥 Into Sprint"
# The state is in the `UiSession`, so a payload only has to name the Card and say whether
# the tap was on its title or on its `↩️ Return`.
_OPEN_PAYLOAD = re.compile(r"^sp-(\d{1,9})$")
_RETURN_PAYLOAD = re.compile(r"^sr-(\d{1,9})$")


_BURST_WARNING = (
    "⏳ That is a lot of link taps. Telegram counts them against your account, not this "
    "bot, and can stop opening any bot for hours."
)
# The tap reaches Telegram before it reaches the bot, so nothing here can prevent one.
# Saying what is happening is the only thing left.
_link_taps: deque[float] = deque(maxlen=PLAN_LINK_BURST_TAPS)


def _is_a_burst() -> bool:
    now = monotonic()
    _link_taps.append(now)
    return (
        len(_link_taps) == _link_taps.maxlen
        and now - _link_taps[0] < PLAN_LINK_BURST_SECONDS
    )


def is_plan_link(text: str | None) -> bool:
    """Whether this owner message is a tap in the plan table.

    A screen is only ever the last message, and such a tap replaces the screen rather than
    opening another one, so the command middleware must leave that screen alive.
    """
    payload = start_payload(text)
    if payload is None:
        return False
    return bool(_OPEN_PAYLOAD.fullmatch(payload) or _RETURN_PAYLOAD.fullmatch(payload))



def _link(services: Services, text: str, payload: str) -> str:
    """One tappable cell. Without a bot username there is no link to build."""
    if not services.bot_username:
        return text
    return f'<a href="https://t.me/{services.bot_username}?start={payload}">{text}</a>'


def _table(services: Services, planned: list[Card]) -> str:
    rows = [
        '<tr><th align="left">Planned in Sprint</th><th align="right">EP</th>'
        '<th align="center"></th></tr>'
    ]
    if not planned:
        rows.append('<tr><td colspan="3" align="center">Nothing planned yet.</td></tr>')
    for card in planned:
        title = _link(services, html.escape(card.title), f"sp-{card.id}")
        back = _link(services, _RETURN, f"sr-{card.id}")
        rows.append(
            f'<tr><td align="left">{title}</td>'
            f'<td align="right">{card.effort_points or 0}</td>'
            f'<td align="center">{back}</td></tr>'
        )
    return "<table bordered striped>" + "".join(rows) + "</table>"


def _button_label(card: Card) -> str:
    """A keyboard label is half a row wide, so the title is cut. The table is not."""
    title = card.title
    if len(title) > SPRINT_PLAN_TITLE_LIMIT:
        title = f"{title[: SPRINT_PLAN_TITLE_LIMIT - 1]}…"
    return f"{title} ({card.effort_points or 0})"


async def _markup(
    session: AsyncSession,
    services: Services,
    shown: Page,
    *,
    filters: list[int],
    backlog_total: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for card in shown.items:
        rows.append(
            [
                await token_button(
                    session, services.owner_id, _button_label(card), "plan_card", {"id": card.id}
                ),
                await token_button(
                    session, services.owner_id, _INTO_SPRINT, "plan_move", {"id": card.id}
                ),
            ]
        )
    if filters and not shown.items:
        # The keyboard is empty because the filter says so, not because the Backlog is.
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"0 of {backlog_total} Actions match",
                    "plan_page",
                    {},
                )
            ]
        )
    rows.append(
        [
            await token_button(
                session,
                services.owner_id,
                f"🔎 Apply filter ({len(filters)})" if filters else "🔎 Apply filter",
                "plan_filters",
                {},
            )
        ]
    )

    async def step(offset: int, glyph: str) -> InlineKeyboardButton:
        index = min(max(shown.index + offset, 0), shown.count - 1)
        return await token_button(
            session, services.owner_id, glyph, "plan_page", {"page": index}
        )

    rows.append(
        [
            await step(-1, "⬅️"),
            await token_button(
                session,
                services.owner_id,
                f"{shown.index + 1}/{shown.count}",
                "plan_page",
                {"page": shown.index},
            ),
            await step(1, "➡️"),
        ]
    )
    rows.append([await token_button(session, services.owner_id, "↩️ Back", "sprint_back", {})])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_plan(
    message: Message,
    services: Services,
    *,
    page: int | None = None,
    filters: list[int] | None = None,
    replace_message_id: int | None = None,
) -> None:
    """Draw the plan and record the state it is showing.

    `page` and `filters` default to what the live screen already has, so paging, moving a
    Card and coming back from one all carry nothing.
    """
    async with services.sessions() as session:
        stored = await load_plan_state(session, services.owner_id)
        picked = list(stored.get("filters", [])) if filters is None else list(filters)
        live, matched = await resolve_filters(session, picked, services.views)
        planned = await actions_on_stages(session, CardStage.SPRINT, CardStage.TODAY)
        backlog = await actions_on_stages(session, CardStage.BACKLOG)
        planned.sort(key=lambda card: card.id)
        backlog.sort(key=lambda card: card.id)
        selectable = backlog if matched is None else [c for c in backlog if c.id in matched]
        shown = paginate(
            selectable,
            int(stored.get("page", 0)) if page is None else page,
            SPRINT_PLAN_PAGE_SIZE,
        )
        markup = await _markup(
            session, services, shown, filters=live, backlog_total=len(backlog)
        )
        cost, warning = plan_cost(planned, await capacity_effort_points(session))
        await session.commit()
    body = (
        "<p><b>Sprint plan</b></p>"
        f"<p>In Sprint: {cost}</p>"
        + (f"<p>{warning}</p>" if warning else "")
        + _table(services, planned)
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
        screen_id = replace_message_id
    else:
        sent = await send_registered(
            message, services, body, kind=MessageKind.DASHBOARD, markup=markup, rich=True
        )
        screen_id = sent.message_id
    async with services.sessions() as session:
        await store_state(
            session,
            services.owner_id,
            {"message_id": screen_id, "page": shown.index, "filters": live},
        )
        await session.commit()


async def render_plan_filters(message: Message, services: Services) -> None:
    """Pick the saved Requests that narrow the Backlog. Every picked one has to match."""
    async with services.sessions() as session:
        state = await load_plan_state(session, services.owner_id)
        picked = set(state.get("filters", []))
        requests = list(
            await session.scalars(
                select(SavedRequest).order_by(SavedRequest.name)
            )
        )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"{'☑' if request.id in picked else '☐'} {request.name}"[:60],
                    "plan_filter_toggle",
                    {"id": request.id},
                )
            ]
            for request in requests
        ]
        rows.append([await token_button(session, services.owner_id, "↩️ Back", "plan_page", {})])
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


async def handle_plan_start(message: Message, services: Services, payload: str) -> bool:
    """Act on a tap in the plan table, in place of the screen it was made on.

    False means the payload belongs to someone else. The screen survived the command
    middleware, so it is still the last message and the answer takes its place there.
    """
    returned = _RETURN_PAYLOAD.fullmatch(payload)
    opened = _OPEN_PAYLOAD.fullmatch(payload)
    if returned is None and opened is None:
        return False
    async with services.sessions() as session:
        state = await load_plan_state(session, services.owner_id)
        screen_id = await plan_screen_id(session, message.chat.id, state)
    if returned is not None:
        async with services.sessions() as session:
            card = await session.get(Card, int(returned[1]))
            if card is not None:
                await move_card(session, card.id, CardStage.BACKLOG)
            await session.commit()
        await render_plan(message, services, replace_message_id=screen_id)
    else:
        await render_card(
            message,
            services,
            int(opened[1]),
            replace_message_id=screen_id,
            back=plan_back(state),
        )
    if _is_a_burst():
        await send_toast(message, services, _BURST_WARNING)
    return True


async def on_plan_open(context: CallbackContext) -> None:
    """Enter planning from the Sprint screen. A fresh visit starts unfiltered on page 1."""
    await render_plan(context.message, context.services, page=0, filters=[])


async def on_plan_page(context: CallbackContext) -> None:
    page = context.payload.get("page")
    await render_plan(
        context.message, context.services, page=None if page is None else int(page)
    )


async def on_plan_move(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await move_card(session, int(context.payload["id"]), CardStage.SPRINT)
        await session.commit()
    await render_plan(context.message, context.services)


async def on_plan_card(context: CallbackContext) -> None:
    async with context.sessions() as session:
        state = await load_plan_state(session, context.owner_id)
    await render_card(
        context.message, context.services, int(context.payload["id"]), back=plan_back(state)
    )


async def on_plan_filters(context: CallbackContext) -> None:
    await render_plan_filters(context.message, context.services)


async def on_plan_filter_toggle(context: CallbackContext) -> None:
    request_id = int(context.payload["id"])
    async with context.sessions() as session:
        state = await load_plan_state(session, context.owner_id)
        picked = list(state.get("filters", []))
        if request_id in picked:
            picked.remove(request_id)
        else:
            picked.append(request_id)
        # A different Backlog is a different page count, so the page cannot survive.
        await store_state(
            session, context.owner_id, {**state, "filters": picked, "page": 0}
        )
        await session.commit()
    await render_plan_filters(context.message, context.services)
