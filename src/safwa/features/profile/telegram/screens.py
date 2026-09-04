"""The `/settings` screen and its focused field prompts."""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import SystemClock
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Services,
    TextInputScreen,
    edit_registered_message,
    menu_row,
    render_text_input,
    send_registered,
    token_button,
    with_notice,
)
from tg_agent_shell.telegram.contributions import TextInputFlow
from tg_agent_shell.telegram.model import UiSession

from ....constants import SPRINT_LENGTH_MAX_DAYS, SPRINT_LENGTH_MIN_DAYS
from ....foundation.workspace import Workspace
from ...reminders.api import parse_clock_or_off
from ..model import UserProfile
from ..use_cases import profile_field, set_profile_field


@dataclass(frozen=True, slots=True)
class SettingsField:
    """One profile value edited through a button and a focused prompt."""

    title: str
    label: str
    instruction: str
    parse: Callable[[str], Any]
    show: Callable[[Any], str]


def _parse_sprint_length(raw: str) -> int:
    if not raw.isdigit() or not SPRINT_LENGTH_MIN_DAYS <= int(raw) <= SPRINT_LENGTH_MAX_DAYS:
        raise ValueError(
            f"Send a whole number between {SPRINT_LENGTH_MIN_DAYS} and {SPRINT_LENGTH_MAX_DAYS}."
        )
    return int(raw)


def _parse_capacity(raw: str) -> int | None:
    if raw.lower() == "off":
        return None
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError("Send a positive number of effort points, or off.")
    return int(raw)


def _parse_daily_time(raw: str) -> time | None:
    try:
        return parse_clock_or_off(raw)
    except ValueError:
        raise ValueError("Send a time as HH:MM, for example 22:00, or off.") from None


def _clock(value: time | None) -> str:
    return value.strftime("%H:%M") if value else "off"


SETTINGS_FIELDS: dict[str, SettingsField] = {
    "about_me": SettingsField(
        title="About me",
        label="👤 About me",
        instruction="Send what Safwa should know about you. Send off to clear it.",
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
    "advisor_instructions": SettingsField(
        title="Advisor instructions",
        label="🧭 Advisor instructions",
        instruction="Send standing instructions for Safwa. Send off to clear them.",
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
    "sprint_length_days": SettingsField(
        title="Sprint length",
        label="🏁 Sprint length",
        instruction=(
            f"Send a number of days between {SPRINT_LENGTH_MIN_DAYS} and "
            f"{SPRINT_LENGTH_MAX_DAYS}. It applies to the next Sprint you start."
        ),
        parse=_parse_sprint_length,
        show=lambda value: f"{value} days",
    ),
    "capacity_effort_points": SettingsField(
        title="Sprint capacity",
        label="🎯 Sprint capacity",
        instruction="Send the effort points one Sprint holds, or off to stop tracking it.",
        parse=_parse_capacity,
        show=lambda value: f"{value} EP" if value else "off",
    ),
    "memory_update_time": SettingsField(
        title="Memory sync",
        label="🧠 Memory sync",
        instruction=(
            "Send the local time the dialogue is folded into memory.md, as HH:MM, or off."
        ),
        parse=_parse_daily_time,
        show=_clock,
    ),
    "diary_time": SettingsField(
        title="Diary",
        label="📔 Diary time",
        instruction=(
            "Send the local time Safwa writes up your day, as HH:MM, or off to stop asking."
        ),
        parse=_parse_daily_time,
        show=_clock,
    ),
    "diary_instructions": SettingsField(
        title="Diary instruction",
        label="✍️ Diary instruction",
        instruction=(
            "Send a standing instruction for the Diary — what to always notice, or how to "
            "write it. Send off to drop it."
        ),
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
}


def settings_text(profile: UserProfile, timezone: str) -> str:
    """Render the settings values; timezone is deliberately display-only."""
    lines = [
        "<b>Settings</b>",
        f"About me: {html.escape(profile.about_me or '—')}",
        f"Advisor instructions: {html.escape(profile.advisor_instructions or '—')}",
    ]
    for name, field in SETTINGS_FIELDS.items():
        lines.append(f"{field.title}: {html.escape(field.show(getattr(profile, name)))}")
    lines.append(f"Timezone: {html.escape(timezone)}")
    lines.append("Tap a setting to change it.")
    return "\n".join(lines)


async def command_settings(
    message: Message,
    services: Services,
    *,
    notice: str | None = None,
    replace_message_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        profile = await session.get(UserProfile, 1)
        workspace = await session.get(Workspace, 1)
        if profile is None or workspace is None:
            raise DomainError("Workspace is not initialized")
        buttons = []
        for name, field in SETTINGS_FIELDS.items():
            buttons.append(
                await token_button(
                    session, services.owner_id, field.label, "settings_edit", {"field": name}
                )
            )
        rendered = settings_text(profile, workspace.timezone)
        await session.commit()
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    text = with_notice(rendered, notice)
    markup = InlineKeyboardMarkup(inline_keyboard=[*rows, menu_row()])
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
        )
    else:
        await send_registered(
            message, services, text, kind=MessageKind.DASHBOARD, markup=markup
        )


async def render_settings_field_prompt(
    message: Message, services: Services, field_name: str, *, notice: str | None = None
) -> None:
    field = SETTINGS_FIELDS[field_name]
    async with services.sessions() as session:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            raise DomainError("Workspace is not initialized")
        current = field.show(getattr(profile, field_name))
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title=field.title,
            current_value=current,
            instruction=field.instruction,
            back_action="settings_back",
            back_payload={},
        ),
        state={"flow": "settings", "field": field_name},
        notice=notice,
    )


def _settings_field(state: Mapping[str, Any]) -> SettingsField:
    field = SETTINGS_FIELDS.get(str(state["field"]))
    if field is None:
        raise DomainError("That setting is no longer available.")
    return field


async def _apply_setting(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: Any
) -> None:
    del services
    await set_profile_field(
        session, profile_field(str(state["field"])), value, clock=SystemClock()
    )


async def _render_settings(
    message: Any, services: Any, state: Mapping[str, Any], value: Any
) -> None:
    del value
    await command_settings(
        message,
        services,
        notice=f"{_settings_field(state).title} updated.",
        replace_message_id=int(state["text_input"]["message_id"]),
    )


TEXT_INPUT = TextInputFlow(
    name="settings",
    validator=lambda state: _settings_field(state).parse,
    apply=_apply_setting,
    render=_render_settings,
)


async def _on_edit(context: CallbackContext) -> None:
    await render_settings_field_prompt(
        context.message, context.services, str(context.payload["field"])
    )


async def _on_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
    await command_settings(context.message, context.services)


SETTINGS_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "settings_edit": _on_edit,
    "settings_back": _on_back,
}
