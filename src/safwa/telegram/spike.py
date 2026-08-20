"""Throwaway spike for the Sprint planning screen: the Sprint is a table, the work is
the keyboard.

The message carries what is already planned — one row per Card, its title a link that
opens it in place of the screen and comes back to the same page, and its effort points
beside it. The keyboard under it is one side at a time:
Backlog rows pull a Card in with `📥`, Sprint rows send it back with `↩️`, and one button
swaps the side. Only Cards it created are shown and moved, so production planning data is
never touched.

Delete this module, the `/spike` command and the `spike_*` callbacks once the real screen
is decided.
"""

from __future__ import annotations

import html
import re
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import SPRINT_PLAN_PAGE_SIZE, SPRINT_PLAN_TITLE_LIMIT
from ..domain import create_card, delete_subtree, move_card
from ..enums import CardKind, CardStage, MessageKind
from ..models import Card, TelegramMessage, UserProfile
from ._core import CallbackContext, Services
from ._messaging import edit_registered_message, send_registered, token_button
from ._presentation import Page, menu_row, paginate, start_payload
from .cards import render_card

SPIKE_PREFIX = "Spike"
_SEED_BACKLOG = 23
_SEED_SPRINT = 6
_SEED_EFFORTS = (1, 2, 3, 5, 8, 13)
_SPRINT_STAGES = (CardStage.SPRINT.value, CardStage.TODAY.value)
_TO_SPRINT = "📥 Into Sprint"
_RETURN = "↩️ Return"
# U+2800 is a printable character Telegram accepts and draws as nothing, so a short page
# keeps the grid rather than stretching its last row.
_FILLER = "⠀"
# A deep-link payload accepts only [A-Za-z0-9_-], so the Card and the page to come back to
# are matched rather than parsed: id, page index, and which side the keyboard was showing.
_OPEN_PAYLOAD = re.compile(r"^sp-(\d{1,9})-(\d{1,4})-([01])$")
_RETURN_PAYLOAD = re.compile(r"^sr-(\d{1,9})-(\d{1,4})-([01])$")


def is_spike_link(text: str | None) -> bool:
    """Whether this owner message is a tap on a title in the table.

    A screen is only ever the last message, and this tap replaces the screen rather than
    opening another one, so the command middleware must leave that screen alive.
    """
    payload = start_payload(text)
    if payload is None:
        return False
    return bool(_OPEN_PAYLOAD.fullmatch(payload) or _RETURN_PAYLOAD.fullmatch(payload))


async def _live_screen_id(session: AsyncSession, chat_id: int) -> int | None:
    """The screen this tap belongs to: the newest dashboard the bot has in this chat."""
    return await session.scalar(
        select(TelegramMessage.message_id)
        .where(
            TelegramMessage.chat_id == chat_id,
            TelegramMessage.direction == "out",
            TelegramMessage.kind == MessageKind.DASHBOARD.value,
        )
        .order_by(TelegramMessage.message_id.desc())
    )


async def _spike_cards(session: AsyncSession) -> list[Card]:
    return list(
        await session.scalars(
            select(Card)
            .where(Card.title.startswith(SPIKE_PREFIX), Card.archived_at.is_(None))
            .order_by(Card.id)
        )
    )


async def _seed(session: AsyncSession) -> None:
    if await _spike_cards(session):
        return
    for index in range(_SEED_BACKLOG + _SEED_SPRINT):
        tail = "with a long title that has to be truncated" if index % 3 else "short"
        await create_card(
            session,
            kind=CardKind.ACTION,
            title=f"{SPIKE_PREFIX} {index + 1:02d} {tail}",
            stage=CardStage.SPRINT if index >= _SEED_BACKLOG else CardStage.BACKLOG,
            effort_points=_SEED_EFFORTS[index % len(_SEED_EFFORTS)],
        )


def _button_label(card: Card) -> str:
    """A keyboard label is half a row wide, so the title is cut. The table is not."""
    title = card.title
    if len(title) > SPRINT_PLAN_TITLE_LIMIT:
        title = f"{title[: SPRINT_PLAN_TITLE_LIMIT - 1]}…"
    return f"{title}({card.effort_points or 0})"


def _link(services: Services, text: str, payload: str) -> str:
    """One tappable cell. Without a bot username there is no link to build."""
    if not services.bot_username:
        return text
    return f'<a href="https://t.me/{services.bot_username}?start={payload}">{text}</a>'


def _table(services: Services, planned: list[Card], page: int, sprint: bool) -> str:
    """The Sprint, one row per Card. The page and side ride in every link, so opening a
    Card and coming back lands on the keyboard the tap was made from."""
    rows = [
        '<tr><th align="left">Planned in Sprint</th><th align="right">EP</th>'
        '<th align="center"></th></tr>'
    ]
    if not planned:
        rows.append('<tr><td colspan="3" align="center">Nothing planned yet.</td></tr>')
    for card in planned:
        where = f"{page}-{int(sprint)}"
        title = _link(services, html.escape(card.title), f"sp-{card.id}-{where}")
        back = _link(services, _RETURN, f"sr-{card.id}-{where}")
        effort = card.effort_points or 0
        rows.append(
            f'<tr><td align="left">{title}</td><td align="right">{effort}</td>'
            f'<td align="center">{back}</td></tr>'
        )
    return "<table bordered striped>" + "".join(rows) + "</table>"


def _plain(planned: list[Card], shown: Page) -> str:
    """The same data as an ordinary parse-mode message, to test editing rich back to text."""
    lines = ["<b>Planned in Sprint</b>"]
    lines.extend(
        f"• {html.escape(card.title)} ({card.effort_points or 0})" for card in planned
    )
    lines.append("<b>Keyboard page</b>")
    lines.extend(f"• {html.escape(_button_label(card))}" for card in shown.items)
    return "\n".join(lines)


async def _noop(
    session: AsyncSession, services: Services, text: str, state: dict[str, Any]
) -> InlineKeyboardButton:
    """A button that renders the same screen again. Its token has to be reissued, or a
    second tap on it reports an expired action."""
    return await token_button(session, services.owner_id, text, "spike_page", dict(state))


async def _markup(
    session: AsyncSession,
    services: Services,
    shown: Page,
    *,
    plain: bool,
    sprint: bool,
) -> InlineKeyboardMarkup:
    state: dict[str, Any] = {"p": shown.index, "plain": plain, "s": sprint}
    action_label = _RETURN if sprint else _TO_SPRINT
    action_stage = CardStage.BACKLOG.value if sprint else CardStage.SPRINT.value
    rows: list[list[InlineKeyboardButton]] = []
    for card in shown.items:
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    _button_label(card),
                    "spike_open",
                    {**state, "id": card.id},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    action_label,
                    "spike_move",
                    {**state, "id": card.id, "stage": action_stage},
                ),
            ]
        )
    if not shown.items:
        rows.append([await _noop(session, services, _FILLER, state) for _ in range(2)])

    async def step(offset: int, glyph: str) -> InlineKeyboardButton:
        index = min(max(shown.index + offset, 0), shown.count - 1)
        return await token_button(
            session, services.owner_id, glyph, "spike_page", {**state, "p": index}
        )

    rows.append(
        [
            await step(-1, "⬅️"),
            await _noop(session, services, f"{shown.index + 1}/{shown.count}", state),
            await step(1, "➡️"),
        ]
    )
    # The page index belongs to the side it was read on, so swapping starts at the top.
    rows.append(
        [
            await token_button(
                session,
                services.owner_id,
                "📋 Backlog side" if sprint else "🏃 Sprint side",
                "spike_page",
                {**state, "p": 0, "s": not sprint},
            )
        ]
    )
    rows.append(
        [
            await token_button(
                session,
                services.owner_id,
                "🧩 Rich table" if plain else "🔤 Plain text",
                "spike_page",
                {**state, "plain": not plain},
            )
        ]
    )
    rows.append(
        [await token_button(session, services.owner_id, "🧹 Delete spike Cards", "spike_clean")]
    )
    rows.append(menu_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_spike(
    message: Message,
    services: Services,
    *,
    page: int = 0,
    plain: bool = False,
    sprint: bool = False,
    replace_message_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        cards = await _spike_cards(session)
        profile = await session.get(UserProfile, 1)
        capacity = profile.capacity_effort_points if profile else None
        planned = [card for card in cards if card.effective_stage in _SPRINT_STAGES]
        backlog = [card for card in cards if card.effective_stage == CardStage.BACKLOG.value]
        shown = paginate(planned if sprint else backlog, page, SPRINT_PLAN_PAGE_SIZE)
        markup = await _markup(session, services, shown, plain=plain, sprint=sprint)
        await session.commit()
    effort = sum(card.effort_points or 0 for card in planned)
    headline = (
        f"In Sprint: {len(planned)} Cards · {effort} EP planned · "
        f"capacity {capacity if capacity is not None else '—'} EP"
    )
    if plain:
        body = f"<b>Sprint plan · spike</b>\n{headline}\n" + _plain(planned, shown)
    else:
        body = f"<p><b>Sprint plan · spike</b></p><p>{headline}</p>" + _table(
            services, planned, shown.index, sprint
        )
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            body,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            rich=not plain,
        )
        return
    await send_registered(
        message,
        services,
        body,
        kind=MessageKind.DASHBOARD,
        markup=markup,
        rich=not plain,
    )


async def run_spike(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        await _seed(session)
        await session.commit()
    await render_spike(message, services)


async def handle_spike_start(message: Message, services: Services, payload: str) -> bool:
    """Open a Card named in the table, in place of the screen it was named on.

    False means the payload belongs to someone else.  The screen survived the command
    middleware, so it is still the last message and the Card takes its place there.
    """
    returned = _RETURN_PAYLOAD.fullmatch(payload)
    if returned is not None:
        async with services.sessions() as session:
            card = await session.get(Card, int(returned[1]))
            if card is not None and card.title.startswith(SPIKE_PREFIX):
                await move_card(session, card.id, CardStage.BACKLOG)
                await session.commit()
            screen_id = await _live_screen_id(session, message.chat.id)
        await render_spike(
            message,
            services,
            page=int(returned[2]),
            sprint=bool(int(returned[3])),
            replace_message_id=screen_id,
        )
        return True
    opened = _OPEN_PAYLOAD.fullmatch(payload)
    if opened is None:
        return False
    async with services.sessions() as session:
        screen_id = await _live_screen_id(session, message.chat.id)
        back = await token_button(
            session,
            services.owner_id,
            "↩️ Back to the plan",
            "spike_page",
            {"p": int(opened[2]), "s": bool(int(opened[3])), "plain": False},
        )
        await session.commit()
    await render_card(
        message,
        services,
        int(opened[1]),
        replace_message_id=screen_id,
        extra_rows=[[back]],
    )
    return True


async def on_spike_page(context: CallbackContext) -> None:
    await render_spike(
        context.message,
        context.services,
        page=int(context.payload.get("p", 0)),
        plain=bool(context.payload.get("plain", False)),
        sprint=bool(context.payload.get("s", False)),
    )


async def on_spike_move(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await move_card(session, int(context.payload["id"]), CardStage(context.payload["stage"]))
        await session.commit()
    await on_spike_page(context)


async def on_spike_open(context: CallbackContext) -> None:
    state = {key: context.payload[key] for key in ("p", "plain", "s") if key in context.payload}
    async with context.sessions() as session:
        back = await token_button(
            session, context.owner_id, "↩️ Back to the plan", "spike_page", state
        )
        await session.commit()
    await render_card(
        context.message, context.services, int(context.payload["id"]), extra_rows=[[back]]
    )


async def on_spike_clean(context: CallbackContext) -> None:
    async with context.sessions() as session:
        for card in await _spike_cards(session):
            await delete_subtree(session, card.id)
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        "Spike Cards deleted.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )
