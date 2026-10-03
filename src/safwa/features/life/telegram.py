"""The Life in weeks screens: the one Retro opens, its pictures, and its Settings.

The screen offers a picture for each thing recorded. A picture arrives as an album of the
whole grid and a close-up, in place of the screen, with a message below it that leads back;
a picture by what the Actions carried also offers each one alone there.
"""

from __future__ import annotations

import asyncio
import html
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Services,
    TextInputScreen,
    dismiss_prior_ui,
    edit_registered_message,
    menu_row,
    render_text_input,
    send_registered,
    token_button,
    with_notice,
)
from tg_agent_shell.telegram.contributions import TextInputFlow

from ..cards.telegram import CATEGORY_EMOJIS, ENERGY_EMOJIS
from .api import life_grid
from .charts import draw_life
from .measures import GROUP_CHARTS, LifeChart, focuses, name_of, offered, picture
from .model import LIFE_YEARS_MAX, LIFE_YEARS_MIN
from .records import life_records, local_today
from .use_cases import life_settings, set_birth_date, set_life_years
from .weeks import heading

CHART_LABELS = {
    LifeChart.FEELING: "😊 Feeling",
    LifeChart.ACTIONS: "✅ Actions",
    LifeChart.EFFORT: "🔢 Effort Points",
    LifeChart.SPRINTS: "🏁 Sprints",
    LifeChart.CATEGORY: "🏷 Categories",
    LifeChart.ENERGY: "⚡ Energy",
    LifeChart.VALUE: "💎 Values",
}
_ABOUT = "A square for every week of your life, a row for every year."


def _rows(buttons: list[InlineKeyboardButton], width: int = 3) -> list[list[InlineKeyboardButton]]:
    return [buttons[index : index + width] for index in range(0, len(buttons), width)]


async def render_life(
    message: Message, services: Services, *, page: int = 0, notice: str | None = None
) -> None:
    """The Life screen: where the owner is, and a button for each picture with something
    recorded to draw. `page` is the Retro list page it leads back to."""
    owner = services.owner_id
    async with services.sessions() as session:
        grid = await life_grid(session)
        rows: list[list[InlineKeyboardButton]] = []
        if grid is None:
            text = f"<b>⏳ Life in weeks</b>\n{_ABOUT}\nSet your birth date in ⚙️ Settings to draw it."
        else:
            records = await life_records(session, utcnow())
            charts = offered(records)
            text = (
                f"<b>⏳ Life in weeks</b>\n{html.escape(heading(grid, records.today, records.since))}"
                f"\n{_ABOUT} "
                + ("Choose what colours the weeks." if charts
                   else "Nothing is recorded yet to colour them with.")
            )
            rows += _rows([
                await token_button(
                    session, owner, CHART_LABELS[chart], "life_chart",
                    {"chart": chart.value, "page": page},
                )
                for chart in charts
            ])
        rows.append([await token_button(session, owner, "⚙️ Settings", "life_settings", {"page": page})])
        rows.append([await token_button(session, owner, "↩️ Back", "retro_list", {"page": page})])
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(text, notice),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[*rows, menu_row()]),
    )


def _focus_label(chart: LifeChart, key: str) -> str:
    emoji = {LifeChart.CATEGORY: CATEGORY_EMOJIS, LifeChart.ENERGY: ENERGY_EMOJIS}.get(chart, {})
    name = name_of(chart, key)
    return f"{emoji[key]} {name}" if key in emoji else name


async def _focus_rows(
    session: AsyncSession, services: Services, chart: LifeChart, chosen: tuple[str, ...],
    focus: str | None, page: int,
) -> list[list[InlineKeyboardButton]]:
    """All of them, then each one alone; the one on the picture now is ticked."""
    picked = focus.casefold() if focus is not None else None
    return _rows([
        await token_button(
            session,
            services.owner_id,
            ("✓ " if (key.casefold() if key else None) == picked else "")
            + (_focus_label(chart, key) if key else "All"),
            "life_chart",
            {"chart": chart.value, "focus": key, "page": page},
        )
        for key in (None, *chosen)
    ])


async def send_picture(
    message: Message,
    services: Services,
    chart: LifeChart,
    focus: str | None = None,
    *,
    page: int = 0,
) -> None:
    """The picture as one album in place of the screen it was asked from, and below it the
    message that leads back to the Life screen."""
    async with services.sessions() as session:
        grid = await life_grid(session)
        if grid is None:
            raise DomainError("Set your birth date in Life settings first.")
        records = await life_records(session, utcnow())
        shown = picture(chart, records, grid, focus)
        rows = (
            await _focus_rows(session, services, chart, focuses(chart, records), focus, page)
            if chart in GROUP_CHARTS
            else []
        )
        rows.append([
            await token_button(session, services.owner_id, "↩️ Back", "life_open", {"page": page})
        ])
        await session.commit()
    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
    pictures = await asyncio.to_thread(draw_life, shown, grid, records.today, records.since)
    # The album a picture before left goes with it, and an album cannot go above a message
    # already in the chat, so the screen it was asked from goes too.
    await dismiss_prior_ui(message, services)
    if message.from_user and message.from_user.is_bot:
        await services.chat.remove_screen(message, message.message_id)
    await services.chat.send_photos(
        message,
        [BufferedInputFile(png, filename=f"{name}.png") for name, png in pictures],
        kind=MessageKind.DASHBOARD.value,
    )
    text = f"<b>⏳ Life in weeks · {html.escape(shown.title)}</b>\n{html.escape(shown.stats[0])}"
    if chart in GROUP_CHARTS:
        text += "\nTap one to see its share of each week."
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[*rows, menu_row()]),
        replace=False,
    )


# ----------------------------------------------------------------------------- Settings


@dataclass(frozen=True, slots=True)
class _Field:
    """One setting, edited through a button and a typed value."""

    title: str
    label: str
    instruction: str
    parse: Callable[[str], Any]
    show: Callable[[Any], str]


def _parse_birth_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError("Send a date as YYYY-MM-DD, for example 1992-03-14.") from None


def _parse_years(raw: str) -> int:
    if not raw.isdigit() or not LIFE_YEARS_MIN <= int(raw) <= LIFE_YEARS_MAX:
        raise ValueError(
            f"Send a whole number of years from {LIFE_YEARS_MIN} to {LIFE_YEARS_MAX}."
        )
    return int(raw)


LIFE_FIELDS: dict[str, _Field] = {
    "birth_date": _Field(
        title="Birth date",
        label="🎂 Birth date",
        instruction="Send your birth date as YYYY-MM-DD, for example 1992-03-14.",
        parse=_parse_birth_date,
        show=lambda value: value.isoformat() if value else "not set",
    ),
    "years": _Field(
        title="Years in the grid",
        label="📏 Years",
        instruction=(
            f"Send how many years the grid holds, a whole number from {LIFE_YEARS_MIN} to "
            f"{LIFE_YEARS_MAX}. Past your age it grows by itself."
        ),
        parse=_parse_years,
        show=str,
    ),
}


def _field(state: Mapping[str, Any]) -> _Field:
    field = LIFE_FIELDS.get(str(state["field"]))
    if field is None:
        raise DomainError("That setting is no longer available.")
    return field


async def render_settings(
    message: Message,
    services: Services,
    *,
    page: int = 0,
    notice: str | None = None,
    replace_message_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        settings = await life_settings(session)
        rows = [
            [
                await token_button(
                    session, services.owner_id, field.label, "life_edit",
                    {"field": name, "page": page},
                )
                for name, field in LIFE_FIELDS.items()
            ],
            [await token_button(session, services.owner_id, "↩️ Back", "life_open", {"page": page})],
            menu_row(),
        ]
        await session.commit()
    text = with_notice(
        "<b>⚙️ Life settings</b>\n"
        + "\n".join(
            f"{field.title}: {html.escape(field.show(getattr(settings, name)))}"
            for name, field in LIFE_FIELDS.items()
        ),
        notice,
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message, services, replace_message_id, text, kind=MessageKind.DASHBOARD,
            markup=markup,
        )
    else:
        await send_registered(message, services, text, kind=MessageKind.DASHBOARD, markup=markup)


async def _apply_field(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: Any
) -> None:
    del services
    if state["field"] == "birth_date":
        await set_birth_date(session, value, await local_today(session, utcnow()))
    else:
        await set_life_years(session, value)


async def _render_settings(
    message: Any, services: Any, state: Mapping[str, Any], value: Any
) -> None:
    del value
    await render_settings(
        message,
        services,
        page=int(state.get("page") or 0),
        notice=f"{_field(state).title} updated.",
        replace_message_id=int(state["text_input"]["message_id"]),
    )


TEXT_INPUT = TextInputFlow(
    name="life",
    validator=lambda state: _field(state).parse,
    apply=_apply_field,
    render=_render_settings,
)


# ------------------------------------------------------------------------------ buttons


def _page(context: CallbackContext) -> int:
    return int(context.payload.get("page") or 0)


async def _on_open(context: CallbackContext) -> None:
    """The Life screen in place of the one tapped, taking a picture's album with it."""
    await dismiss_prior_ui(context.message, context.services)
    await render_life(context.message, context.services, page=_page(context))


async def _on_chart(context: CallbackContext) -> None:
    await send_picture(
        context.message,
        context.services,
        LifeChart(context.payload["chart"]),
        context.payload.get("focus"),
        page=_page(context),
    )


async def _on_settings(context: CallbackContext) -> None:
    # Also the way back from a setting's prompt, which ends with it.
    await dismiss_prior_ui(context.message, context.services)
    await render_settings(context.message, context.services, page=_page(context))


async def _on_edit(context: CallbackContext) -> None:
    name = str(context.payload["field"])
    field = _field(context.payload)
    async with context.sessions() as session:
        current = field.show(getattr(await life_settings(session), name))
    await render_text_input(
        context.message,
        context.services,
        screen=TextInputScreen(
            title=field.title,
            current_value=current,
            instruction=field.instruction,
            back_action="life_settings",
            back_payload={"page": _page(context)},
        ),
        state={"flow": "life", "field": name, "page": _page(context)},
    )


LIFE_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "life_open": _on_open,
    "life_chart": _on_chart,
    "life_settings": _on_settings,
    "life_edit": _on_edit,
}
