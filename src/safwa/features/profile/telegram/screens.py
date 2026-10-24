"""The Profile, its field prompts, and the automatic reaction screens."""

from __future__ import annotations

import asyncio
import html
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.access.credentials import hash_secret_word, matches_secret_word
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.hooks.contracts import HookSpec
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    TextInputAction,
    TextInputScreen,
    edit_registered_message,
    markdown_field,
    menu_row,
    render_text_input,
    send_registered,
    token_button,
    with_notice,
)
from tg_agent_shell.telegram.contributions import TextInputFlow
from tg_agent_shell.telegram.model import UiSession

from ....foundation.workspace import Workspace
from ...reminders.api import parse_clock
from ..api import EFFORT_TRACKING_REMINDER, TIME_TRACKING_REMINDER, TODAY_OVERLOAD, set_hook_switch
from ..model import (
    HOME_AFTER_MINUTES_MAX,
    HOME_AFTER_MINUTES_MIN,
    ProfileField,
    UserProfile,
)
from ..use_cases import (
    profile_field,
    require_profile_field,
    set_profile_field,
    set_secret_word,
    validated_profile_value,
)


@dataclass(frozen=True, slots=True)
class EditableField:
    """One profile value edited through a button and a focused prompt."""

    title: str
    label: str
    instruction: str
    parse: Callable[[str], Any]
    show: Callable[[Any], str]
    # (services, parsed) -> the value stored, for a field a model reads.
    read: Callable[[Any, Any], Awaitable[Any]] | None = None


def _parse_home_after(raw: str) -> int:
    if not raw.isdigit() or not HOME_AFTER_MINUTES_MIN <= int(raw) <= HOME_AFTER_MINUTES_MAX:
        raise ValueError(
            f"Send a whole number of minutes between {HOME_AFTER_MINUTES_MIN} and "
            f"{HOME_AFTER_MINUTES_MAX}."
        )
    return int(raw)


def _parse_clock(raw: str) -> time:
    try:
        return parse_clock(raw)
    except ValueError:
        raise ValueError("Send a time as HH:MM, for example 09:00.") from None


def _parse_today_capacity(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ValueError("Send a positive number of Effort Points.") from None
    validated_profile_value(ProfileField.TODAY_CAPACITY_EP, value)
    return value


def _clock(value: time | None) -> str:
    return value.strftime("%H:%M") if value else "off"


def _windows(value: list[str] | str) -> str:
    """Don't disturb as stored, or the words a proposal has not read yet."""
    if isinstance(value, str):
        return value
    return ", ".join(item.replace("-", "–") for item in value) or "off"


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
    "evening_time": EditableField(
        title="Evening time",
        label="🌙 Evening time",
        instruction=(
            "Send the local time Safwa sums up your day and offers to write the Diary, "
            "as HH:MM. Switch the Daily summary and the Diary nudge off in Profile → Hooks."
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
    "motivator_instructions": EditableField(
        title="Motivator instructions",
        label="💎 Motivator instructions",
        instruction=(
            "Send instructions for the Home motivator, such as your preferred style or "
            "what to emphasize. Send off to clear them."
        ),
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
    "home_after_minutes": EditableField(
        title="Home after",
        label="🏠 Home after",
        instruction=(
            f"Send how many minutes you may leave the chat, from {HOME_AFTER_MINUTES_MIN} to "
            f"{HOME_AFTER_MINUTES_MAX}, before Safwa empties the chat."
        ),
        parse=_parse_home_after,
        show=lambda value: f"{value} min",
    ),
    "dont_disturb": EditableField(
        title="Don't disturb",
        label="💤 Don't disturb",
        instruction=(
            "Send the hours when Reminders that repeat every few minutes or hours must wait, "
            "such as 22:00–08:00, or 22–8 and 13–14. Send off to remove them all."
        ),
        parse=lambda raw: raw,
        show=_windows,
        read=lambda services, raw: services.features.dont_disturb.windows(raw),
    ),
    "today_capacity_ep": EditableField(
        title="Today capacity",
        label="⚖️ Today capacity",
        instruction="Send the maximum Effort Points for Today.",
        parse=_parse_today_capacity,
        show=lambda value: f"{value:g} EP",
    ),
}


def _field_visible(profile: UserProfile, name: str) -> bool:
    return name != "today_capacity_ep" or (
        profile.effort_tracking and TODAY_OVERLOAD not in profile.disabled_hooks
    )


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


def usage_label(seconds: int) -> str:
    days, minutes = divmod(seconds // 60, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = [f"{value}{unit}" for value, unit in ((days, "d"), (hours, "h"), (minutes, "m")) if value]
    return "~" + (" ".join(parts) or "0m")


def profile_text(profile: UserProfile, timezone: str, *, usage: int = 0) -> str:
    """Render the Profile values; timezone is deliberately display-only."""
    text_fields = {"about_me", "advisor_instructions", "diary_instructions", "motivator_instructions"}
    lines = ["<b>Profile</b>", ""]
    for name, field in PROFILE_FIELDS.items():
        if name in text_fields:
            lines.extend((markdown_field(field.title, getattr(profile, name)), ""))
    for name, field in PROFILE_FIELDS.items():
        if name not in text_fields and _field_visible(profile, name):
            lines.append(f"<i>{field.title}:</i> {html.escape(field.show(getattr(profile, name)))}")
    lines.append(f"<i>Timezone:</i> {html.escape(timezone)}")
    lines.append(f"<i>Usage time:</i> {usage_label(usage)}")
    lines.append(f"<i>Secret word:</i> {'set' if profile.secret_word_hash else 'off'}")
    lines.append(
        f"<i>Effort Points:</i> {'on' if profile.effort_tracking else 'off'} — optional estimates "
        "of Action load, a Sprint's capacity and Today overload warnings."
    )
    lines.append(
        f"<i>Time tracking:</i> {'on' if profile.time_tracking else 'off'} — records the time an "
        f"Action took; your active day runs from the Morning time to the Evening time, "
        f"{_clock(profile.morning_time)} to {_clock(profile.evening_time)}."
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
            if not _field_visible(profile, name):
                continue
            buttons.append(
                await token_button(
                    session, services.owner_id, field.label, "profile_edit", {"field": name}
                )
            )
        usage = await services.usage.seconds(session) if services.usage is not None else 0
        rendered = profile_text(profile, workspace.timezone, usage=usage)
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append([await token_button(
            session, services.owner_id, "🔐 Secret word", "profile_secret_word", {},
        )])
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
        await require_profile_field(session, profile_field(field_name))
        current = field.show(getattr(profile, field_name))
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title=field.title,
            current_value=current,
            instruction=field.instruction,
            markdown=field_name in {
                "about_me", "advisor_instructions", "diary_instructions", "motivator_instructions",
            },
            back=Place("profile_back"),
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


async def _read_field(
    message: Any, services: Any, state: Mapping[str, Any], value: Any
) -> Any:
    read = _editable_field(state).read
    return value if read is None else await read(services, value)


TEXT_INPUT = TextInputFlow(
    name="profile",
    validator=lambda state: _editable_field(state).parse,
    apply=_apply_field,
    render=_render_profile,
    prepare=_read_field,
)


def _secret_word_screen(current: str, *, again: bool) -> TextInputScreen:
    return TextInputScreen(
        title="Secret word", current_value=current,
        instruction=(
            "Send the same Secret word again to save it."
            if again
            else "Send any secret word, even one character, but not a command. Whitespace "
            "and case matter. You send it twice. Use Remove secret word to disable it. "
            "Access locks after automatic clearing; /clear does not lock it."
        ),
        back=Place("profile_back"),
        extra_actions=(TextInputAction("Remove secret word", Place("profile_secret_word_clear")),)
        if current == "set" else (),
    )


async def _check_secret_word(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> tuple[str, str]:
    """The first entry becomes a verifier to compare with; the second is saved if it matches."""
    if value.lstrip().startswith("/"):
        raise ValueError("A command cannot be the Secret word.")
    if "first" not in state:
        return "first", await asyncio.to_thread(hash_secret_word, value)
    if await asyncio.to_thread(matches_secret_word, value, state["first"]):
        return "same", value
    return "different", ""


async def _apply_secret_word(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: tuple[str, str]
) -> None:
    step, word = value
    if step == "same":
        await set_secret_word(session, word)


async def _render_secret_word(
    message: Any, services: Any, state: Mapping[str, Any], value: tuple[str, str]
) -> None:
    step, verifier = value
    if step == "same":
        await command_profile(
            message, services, notice="Secret word updated.",
            replace_message_id=int(state["text_input"]["message_id"]),
        )
        return
    started = {key: item for key, item in state.items() if key != "first"}
    await render_text_input(
        message, services,
        screen=_secret_word_screen(state["current"], again=step == "first"),
        state={**started, "first": verifier} if step == "first" else started,
        notice=None if step == "first" else "The two entries differ. Send the Secret word again.",
    )


SECRET_WORD_INPUT = TextInputFlow(
    name="secret_word", validator=lambda state: None, prepare=_check_secret_word,
    apply=_apply_secret_word, render=_render_secret_word, raw=True,
)


async def _on_secret_word(context: CallbackContext) -> None:
    async with context.sessions() as session:
        profile = await session.get(UserProfile, 1)
        current = "set" if profile is not None and profile.secret_word_hash else "off"
    await render_text_input(
        context.message, context.services,
        screen=_secret_word_screen(current, again=False),
        state={"flow": "secret_word", "current": current},
    )


async def _on_secret_word_clear(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await set_secret_word(session, None)
        await session.execute(delete(UiSession).where(UiSession.owner_id == context.owner_id))
        await session.commit()
    await command_profile(
        context.message, context.services, notice="Secret word removed.",
        replace_message_id=context.message.message_id,
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
            f"<i>Status:</i> {_switch_state(profile, hook)}."
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
    "profile_secret_word": _on_secret_word,
    "profile_secret_word_clear": _on_secret_word_clear,
    "profile_edit": _on_edit,
    "profile_hooks": _on_hooks,
    "profile_hook": _on_hook,
    "profile_hooks_back": _on_hooks_back,
    "profile_switch": _on_switch,
    "profile_time_tracking": _on_time_tracking,
    "profile_effort_tracking": _on_effort_tracking,
    "profile_back": _on_back,
}
