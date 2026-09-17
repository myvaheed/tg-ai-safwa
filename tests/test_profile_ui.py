"""The Profile screen and its focused field prompts."""

from __future__ import annotations

from datetime import time

from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.bootstrap.modules import REGISTRY
from safwa.features.heavy_analyzer.module import HEAVY_ANALYZER_HOOK
from safwa.features.profile.api import hook_switched_on
from safwa.features.profile.model import DIARY_TIME_DEFAULT, ProfileField, UserProfile
from safwa.features.profile.telegram import command_profile
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.summary.module import SUMMARY_HOOK
from tg_agent_shell.hooks.contracts import BeforeTool
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


async def test_ps_hooks_015_a_reaction_with_a_switch_is_turned_off_and_on_in_the_profile(
    sessions,
) -> None:
    """PS-HOOKS-015 — tests/brd/profile.feature"""
    services = services_for(sessions)
    services.hooks = REGISTRY.hooks
    assert HEAVY_ANALYZER_HOOK.agent_related and not SUMMARY_HOOK.agent_related
    message = FakeMessage(940, bot_message=True, answer_as_new=True)
    await command_profile(message, services)

    rendered, markup = message.edits[-1]
    assert "Helper offer: on — Does not run a read past one flat scan" in rendered
    assert "🔔 Helper offer: on" in button_texts(markup)
    assert not any("Automatic Summary" in label for label in button_texts(markup))
    assert "Automatic Summary" not in rendered
    # A complex read the offer would answer, to read the switch through the registry.
    complex_read = BeforeTool(
        run_id=1, agent="root", agent_kind="advisor", tool="query_data", call_id="q1",
        arguments_json='{"sql": "SELECT stage FROM ai_cards GROUP BY stage"}',
    )
    assert len([item async for item in REGISTRY.hooks.evaluate(complex_read, sessions)]) == 1

    async def press(markup) -> tuple[str, object]:
        button = next(
            item
            for row in markup.inline_keyboard
            for item in row
            if item.text.startswith(("🔔 Helper offer", "🔕 Helper offer"))
        )
        await callback_token_handler(
            FakeCallback(button.callback_data.split(":", 1)[1], message), services
        )
        # The screen is redrawn in place, under the message id it was on.
        edited_id, text, edited_markup = message.bot.edits[-1]
        assert edited_id == message.message_id
        return text, edited_markup

    rendered, markup = await press(markup)
    assert "Helper offer switched off." in rendered
    assert "🔕 Helper offer: off" in button_texts(markup)
    # The same process reads the switch live, and a fresh session reads what was stored.
    assert not [item async for item in REGISTRY.hooks.evaluate(complex_read, sessions)]
    async with sessions() as session:
        assert await hook_switched_on(session, HEAVY_ANALYZER_HOOK.name) is False
        assert await hook_switched_on(session, SUMMARY_HOOK.name) is True
        assert (await session.get(UserProfile, 1)).disabled_hooks == [HEAVY_ANALYZER_HOOK.name]

    rendered, markup = await press(markup)
    assert "Helper offer switched on." in rendered
    assert "🔔 Helper offer: on" in button_texts(markup)
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).disabled_hooks == []
