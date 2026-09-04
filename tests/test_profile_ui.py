"""The Profile screen and its focused field prompts."""

from __future__ import annotations

from datetime import time

from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, services_for

from safwa.features.profile.model import DIARY_TIME_DEFAULT, ProfileField, UserProfile
from safwa.features.profile.telegram import command_profile
from safwa.features.profile.use_cases import set_profile_field
from tg_agent_shell.foundation.clock import SystemClock
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.model import UiSession


async def test_valid_profile_input_updates_selected_field_and_auto_closes_prompt(
    sessions,
) -> None:
    """PS-UI-SAVE-008 — tests/brd/profile.feature"""
    services = services_for(sessions)
    async with sessions() as session:
        await set_profile_field(
            session,
            ProfileField.ADVISOR_INSTRUCTIONS,
            "Keep this unchanged.",
            clock=SystemClock(),
        )
        await session.commit()
    message = FakeMessage(921, bot_message=True, answer_as_new=True)
    await command_profile(message, services)

    button = next(
        item
        for row in message.edits[-1][1].inline_keyboard
        for item in row
        if item.text == "👤 About me"
    )
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )

    answer = FakeMessage(922, text="I prefer mornings.", bot_message=False, bot=message.bot)
    await ordinary_text(answer, services)

    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        assert profile.about_me == "I prefer mornings."
        assert profile.advisor_instructions == "Keep this unchanged."
        assert await session.scalar(select(UiSession)) is None
    assert answer.was_deleted is True
    assert "About me: I prefer mornings." in message.bot.edits[-1][1]
    assert "Advisor instructions: Keep this unchanged." in message.bot.edits[-1][1]


async def test_settings_shows_timezone_without_a_timezone_edit_action(sessions) -> None:
    """PS-TIMEZONE-010 — tests/brd/profile.feature"""
    message = FakeMessage(923, bot_message=True, answer_as_new=True)
    await command_profile(message, services_for(sessions))

    rendered, markup = message.edits[-1]
    labels = {item.text for row in markup.inline_keyboard for item in row}
    assert "Timezone: Europe/Istanbul" in rendered
    assert not any("timezone" in label.casefold() for label in labels)


async def test_invalid_settings_input_keeps_data_and_the_same_prompt(sessions) -> None:
    """PS-UI-INVALID-009 — tests/brd/profile.feature"""
    services = services_for(sessions)
    message = FakeMessage(930, bot_message=True, answer_as_new=True)
    await command_profile(message, services)
    button = next(
        item
        for row in message.edits[-1][1].inline_keyboard
        for item in row
        if item.text == "📔 Diary time"
    )
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )

    answer = FakeMessage(931, text="tomorrow", bot_message=False, bot=message.bot)
    await ordinary_text(answer, services)

    assert answer.was_deleted is True
    assert "Send a time as HH:MM" in message.bot.edits[-1][1]
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).diary_time == time.fromisoformat(
            DIARY_TIME_DEFAULT
        )
        ui = await session.scalar(select(UiSession))
        assert (ui.kind, ui.state["field"]) == ("text_input", "diary_time")
