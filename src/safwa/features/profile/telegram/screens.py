"""The Profile, its field prompts, and the automatic reaction screens."""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.hooks.contracts import HookSpec
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
from ....features.cards.api import effort_label
from ....foundation.workspace import Workspace
from ...reminders.api import parse_clock
from ..api import EFFORT_TRACKING_REMINDER, TIME_TRACKING_REMINDER, TODAY_OVERLOAD, set_hook_switch
from ..model import (
    HOME_AFTER_MINUTES_MAX,
    HOME_AFTER_MINUTES_MIN,
    ProfileField,
    UserProfile,
)
from ..use_cases import profile_field, set_profile_field


@dataclass(frozen=True, slots=True)
class EditableField:
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


def _parse_home_after(raw: str) -> int:
    if not raw.isdigit() or not HOME_AFTER_MINUTES_MIN <= int(raw) <= HOME_AFTER_MINUTES_MAX:
        raise ValueError(
            f"Send a whole number of minutes between {HOME_AFTER_MINUTES_MIN} and "
            f"{HOME_AFTER_MINUTES_MAX}."
        )
    return int(raw)


def _parse_capacity(raw: str) -> float | None:
    if raw.lower() == "off":
        return None
    try:
        points = float(raw.replace(",", "."))
    except ValueError:
        raise ValueError("Send a positive number of effort points, or off.") from None
    if points <= 0:
        raise ValueError("Send a positive number of effort points, or off.")
    return points


def _parse_clock(raw: str) -> time:
    try:
        return parse_clock(raw)
    except ValueError:
        raise ValueError("Send a time as HH:MM, for example 09:00.") from None


def _clock(value: time | None) -> str:
    return value.strftime("%H:%M") if value else "off"


PROFILE_FIELDS: dict[str, EditableField] = {
    "about_me": EditableField(
        title="About me",
        label="👤 About me",
        instruction="Send what Safwa should know about you. Send off to clear it.",
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
    "advisor_instructions": EditableField(
        title="Advisor instructions",
        label="🧭 Advisor instructions",
        instruction="Send standing instructions for Safwa. Send off to clear them.",
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
    "sprint_length_days": EditableField(
        title="Sprint length",
        label="🏁 Sprint length",
        instruction=(
            f"Send a number of days between {SPRINT_LENGTH_MIN_DAYS} and "
            f"{SPRINT_LENGTH_MAX_DAYS}. It applies to the next Sprint you start."
        ),
        parse=_parse_sprint_length,
        show=lambda value: f"{value} days",
    ),
    "capacity_effort_points": EditableField(
        title="Sprint capacity",
        label="🎯 Sprint capacity",
        instruction="Send the effort points one Sprint holds, or off to stop tracking it.",
        parse=_parse_capacity,
        show=lambda value: f"{effort_label(value)} EP" if value else "off",
    ),
    "morning_time": EditableField(
        title="Morning time",
        label="🌅 Morning time",
        instruction=(
            "Send the local time Safwa's morning checks run, as HH:MM. Switch each check "
            "off in Profile → Hooks."
        ),
        parse=_parse_clock,
        show=_clock,
    ),
    "diary_time": EditableField(
        title="Diary",
        label="📔 Diary time",
        instruction=(
            "Send the local time Safwa writes up your day, as HH:MM. The Diary nudge is "
            "switched off in Profile → Hooks."
        ),
        parse=_parse_clock,
        show=_clock,
    ),
    "diary_instructions": EditableField(
        title="Diary instruction",
        label="✍️ Diary instruction",
        instruction=(
            "Send a standing instruction for the Diary — what to always notice, or how to "
            "write it. Send off to drop it."
        ),
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
    "summary_time": EditableField(
        title="Daily summary",
        label="🌙 Daily summary",
        instruction=(
            "Send the local time Safwa sums up your day, as HH:MM. The daily summary is "
            "switched off in Profile → Hooks."
        ),
        parse=_parse_clock,
        show=_clock,
    ),
    "home_after_minutes": EditableField(
        title="Home after",
        label="🏠 Home after",
        instruction=(
            f"Send how many minutes you may leave the chat, from {HOME_AFTER_MINUTES_MIN} to "
            f"{HOME_AFTER_MINUTES_MAX}, before Safwa clears it down to the Home dashboard."
        ),
        parse=_parse_home_after,
        show=lambda value: f"{value} min",
    ),
}


def _visible_switches(profile: UserProfile, services: Services) -> tuple[HookSpec, ...]:
    """Show feature-dependent hooks only while their Profile feature is on."""
    return tuple(
        hook
        for hook in services.hooks.agent_related
        if profile.time_tracking or hook.name != TIME_TRACKING_REMINDER
        if profile.effort_tracking or hook.name not in {TODAY_OVERLOAD, EFFORT_TRACKING_REMINDER}
    )


def _time_tracking_label(profile: UserProfile) -> str:
    return f"⌛ Time tracking: {'on' if profile.time_tracking else 'off'}"


def _effort_tracking_label(profile: UserProfile) -> str:
    return f"🔢 Effort Points: {'on' if profile.effort_tracking else 'off'}"


def _switch_state(profile: UserProfile, hook: HookSpec) -> str:
    return "off" if hook.name in profile.disabled_hooks else "on"


def _switch_label(profile: UserProfile, hook: HookSpec) -> str:
    on = _switch_state(profile, hook) == "on"
    return f"{'🔔' if on else '🔕'} {hook.title}: {'on' if on else 'off'}"


def profile_text(profile: UserProfile, timezone: str) -> str:
    """Render the Profile values; timezone is deliberately display-only."""
    lines = [
        "<b>Profile</b>",
        f"About me: {html.escape(profile.about_me or '—')}",
        f"Advisor instructions: {html.escape(profile.advisor_instructions or '—')}",
    ]
    for name, field in PROFILE_FIELDS.items():
        if name == "capacity_effort_points" and not profile.effort_tracking:
            continue
        lines.append(f"{field.title}: {html.escape(field.show(getattr(profile, name)))}")
    lines.append(f"Timezone: {html.escape(timezone)}")
    lines.append(
        f"Effort Points: {'on' if profile.effort_tracking else 'off'} — optional estimates "
        "of Action load, Sprint capacity and Today overload warnings."
    )
    lines.append(
        f"Time tracking: {'on' if profile.time_tracking else 'off'} — records the time an "
        f"Action took; your active day runs from the Morning time to the Diary time, "
        f"{_clock(profile.morning_time)} to {_clock(profile.diary_time)}."
    )
    lines.append("Tap a setting to change it.")
    return "\n".join(lines)


async def command_profile(
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
        for name, field in PROFILE_FIELDS.items():
            if name == "capacity_effort_points" and not profile.effort_tracking:
                continue
            buttons.append(
                await token_button(
                    session, services.owner_id, field.label, "profile_edit", {"field": name}
                )
            )
        rendered = profile_text(profile, workspace.timezone)
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append([
            await token_button(
                session, services.owner_id, _time_tracking_label(profile),
                "profile_time_tracking", {},
            )
        ])
        rows.append([
            await token_button(
                session, services.owner_id, _effort_tracking_label(profile),
                "profile_effort_tracking", {},
            )
        ])
        rows.append([
            await token_button(session, services.owner_id, "🔔 Hooks", "profile_hooks", {})
        ])
        await session.commit()
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


async def render_profile_field_prompt(
    message: Message, services: Services, field_name: str, *, notice: str | None = None
) -> None:
    field = PROFILE_FIELDS[field_name]
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
            back_action="profile_back",
            back_payload={},
        ),
        state={"flow": "profile", "field": field_name},
        notice=notice,
    )


def _editable_field(state: Mapping[str, Any]) -> EditableField:
    field = PROFILE_FIELDS.get(str(state["field"]))
    if field is None:
        raise DomainError("That setting is no longer available.")
    return field


async def _apply_field(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: Any
) -> None:
    del services
    await set_profile_field(session, profile_field(str(state["field"])), value)


async def _render_profile(
    message: Any, services: Any, state: Mapping[str, Any], value: Any
) -> None:
    del value
    await command_profile(
        message,
        services,
        notice=f"{_editable_field(state).title} updated.",
        replace_message_id=int(state["text_input"]["message_id"]),
    )


TEXT_INPUT = TextInputFlow(
    name="profile",
    validator=lambda state: _editable_field(state).parse,
    apply=_apply_field,
    render=_render_profile,
)


async def _on_edit(context: CallbackContext) -> None:
    await render_profile_field_prompt(
        context.message, context.services, str(context.payload["field"])
    )


async def _on_hooks(context: CallbackContext) -> None:
    async with context.sessions() as session:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            raise DomainError("Workspace is not initialized")
        rows = [
            [await token_button(
                session, context.owner_id, _switch_label(profile, hook),
                "profile_hook", {"hook": hook.name},
            )]
            for hook in _visible_switches(profile, context.services)
        ]
        rows.append([
            await token_button(session, context.owner_id, "↩️ Back", "profile_hooks_back", {})
        ])
        await session.commit()
    await edit_registered_message(
        context.message, context.services, context.message.message_id,
        "<b>Hooks</b>\nChoose a hook to read what it does and switch it on or off.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[*rows, menu_row()]),
    )


async def _on_hook(context: CallbackContext, *, notice: str | None = None) -> None:
    name = str(context.payload["hook"])
    async with context.sessions() as session:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            raise DomainError("Workspace is not initialized")
        hook = next(
            (spec for spec in _visible_switches(profile, context.services) if spec.name == name),
            None,
        )
        if hook is None:
            raise DomainError("That setting is no longer available.")
        text = (
            f"<b>{html.escape(hook.title)}</b>\n\n{html.escape(hook.description)}\n\n"
            f"Status: {_switch_state(profile, hook)}."
        )
        rows = [
            [await token_button(
                session, context.owner_id, _switch_label(profile, hook),
                "profile_switch", {"hook": name},
            )],
            [await token_button(session, context.owner_id, "↩️ Back", "profile_hooks", {})],
            menu_row(),
        ]
        await session.commit()
    await edit_registered_message(
        context.message, context.services, context.message.message_id,
        with_notice(text, notice), kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _on_hooks_back(context: CallbackContext) -> None:
    await command_profile(
        context.message, context.services, replace_message_id=context.message.message_id
    )


async def _on_switch(context: CallbackContext) -> None:
    """Flip one automatic reaction and redraw its screen in place."""
    name = str(context.payload["hook"])
    async with context.sessions() as session:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            raise DomainError("Workspace is not initialized")
        switches = _visible_switches(profile, context.services)
        hook = next((spec for spec in switches if spec.name == name), None)
        if hook is None:
            raise DomainError("That setting is no longer available.")
        on = name in profile.disabled_hooks
        await set_hook_switch(
            session, name, on=on, followers=context.services.hooks.followers(name)
        )
        await session.commit()
    await _on_hook(
        context,
        notice=f"{hook.title} switched {'on' if on else 'off'}.",
    )


async def _on_time_tracking(context: CallbackContext) -> None:
    """Flip Time tracking and redraw the Profile in place."""
    async with context.sessions() as session:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            raise DomainError("Workspace is not initialized")
        on = not profile.time_tracking
        await set_profile_field(session, ProfileField.TIME_TRACKING, on)
        await session.commit()
    await command_profile(
        context.message,
        context.services,
        notice=f"Time tracking switched {'on' if on else 'off'}.",
        replace_message_id=context.message.message_id,
    )


async def _on_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
    await command_profile(context.message, context.services)


async def _on_effort_tracking(context: CallbackContext) -> None:
    async with context.sessions() as session:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            raise DomainError("Workspace is not initialized")
        on = not profile.effort_tracking
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, on)
        await session.commit()
    await command_profile(
        context.message, context.services,
        notice=f"Effort Points switched {'on' if on else 'off'}.",
        replace_message_id=context.message.message_id,
    )


PROFILE_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "profile_edit": _on_edit,
    "profile_hooks": _on_hooks,
    "profile_hook": _on_hook,
    "profile_hooks_back": _on_hooks_back,
    "profile_switch": _on_switch,
    "profile_time_tracking": _on_time_tracking,
    "profile_effort_tracking": _on_effort_tracking,
    "profile_back": _on_back,
}
