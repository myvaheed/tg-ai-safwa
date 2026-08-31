"""The Reminder screens: the list, one Reminder, its text and its delete."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.features.profile.model import ProfileField
from safwa.features.profile.telegram import command_settings
from safwa.features.profile.use_cases import DIARY_REMINDER_INSTRUCTION, set_profile_field
from safwa.features.reminders.schedule import resolve
from safwa.features.reminders.telegram import render_reminder, render_reminders
from safwa.features.reminders.use_cases import create_reminder
from safwa.foundation.clock import SystemClock
from safwa.models import Reminder
from safwa.shell import callback_token_handler
from safwa.turn.dialogue import ordinary_text


async def test_reminder_text_requires_a_value_and_restores_its_view(sessions) -> None:
    """RM-WRITE-009 — tests/brd/reminders.feature"""
    async with sessions() as session:
        reminder = await create_reminder(
            session,
            instruction="Take a walk.",
            schedule=resolve(interval_minutes=120, now=datetime.now(UTC), tz=ZoneInfo("UTC")),
            tz=ZoneInfo("UTC"),
        )
        await session.commit()
        reminder_id = reminder.id

    services = services_for(sessions)
    message = FakeMessage(52, bot_message=True)
    await render_reminder(message, services, reminder_id)
    edit = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == "✏️ Text"
    )
    await callback_token_handler(FakeCallback(edit.callback_data.split(":", 1)[1], message), services)
    assert "Current value:\n<pre>Take a walk.</pre>" in message.bot.edits[-1][1]

    invalid = FakeMessage(53, text=" ", bot_message=False, bot=message.bot)
    await ordinary_text(invalid, services)
    assert "Reminder text cannot be empty" in message.bot.edits[-1][1]

    valid = FakeMessage(54, text="Walk around the block.", bot_message=False, bot=message.bot)
    await ordinary_text(valid, services)
    assert valid.was_deleted is True
    assert "Walk around the block." in message.bot.edits[-1][1]


async def test_the_reminders_screen_lists_opens_and_confirms_a_delete(sessions) -> None:
    """RM-UI-023 — tests/brd/reminders.feature"""
    services = services_for(sessions)
    empty = FakeMessage(60, bot_message=True)
    await render_reminders(empty, services)
    assert "Ask your advisor" in empty.edits[-1][0]  # no creation button, and it says why

    async with sessions() as session:
        soon = await create_reminder(
            session,
            instruction="Take a walk.",
            schedule=resolve(interval_minutes=120, now=datetime.now(UTC), tz=ZoneInfo("UTC")),
            tz=ZoneInfo("UTC"),
        )
        await create_reminder(
            session,
            instruction="Review the launch plan.",
            schedule=resolve(days=["Mon"], clock="08:30", now=datetime.now(UTC), tz=ZoneInfo("UTC")),
            tz=ZoneInfo("UTC"),
        )
        await session.commit()
        soon_id = soon.id

    listing = FakeMessage(61, bot_message=True)
    await render_reminders(listing, services)
    labels = button_texts(listing.edits[-1][1])
    assert labels[0] == "every 2 hours · Take a walk."  # the schedule, then the text
    assert labels[1].startswith("every Mon at 08:30 · ")  # next fire first

    detail = FakeMessage(62, bot_message=True)
    await render_reminder(detail, services, soon_id)
    text, markup = detail.edits[-1]
    assert "every 2 hours · next " in text
    assert "Take a walk." in text
    assert "Timing is set through your advisor." in text
    assert button_texts(markup) == ["✏️ Text", "🗑 Delete", "↩️ Back"]

    remove = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text == "🗑 Delete"
    )
    await callback_token_handler(
        FakeCallback(remove.callback_data.split(":", 1)[1], detail), services
    )
    prompt_text, prompt_markup = detail.edits[-1]
    assert "Delete this Reminder?" in prompt_text
    assert button_texts(prompt_markup) == ["Delete Reminder", "↩️ Back"]
    async with sessions() as session:
        assert await session.get(Reminder, soon_id) is not None  # one confirmation, not none


async def test_the_reminders_screen_and_settings_hide_safwas_own_reminder(sessions) -> None:
    """RM-SYSTEM-022 — tests/brd/reminders.feature"""
    # The owner sets the Diary in Settings; the Reminder behind it is not theirs to see.
    async with sessions() as session:
        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(22, 0), clock=SystemClock()
        )
        await create_reminder(
            session,
            instruction="Check my posture.",
            schedule=resolve(interval_minutes=120, now=datetime.now(UTC), tz=ZoneInfo("UTC")),
            tz=ZoneInfo("UTC"),
        )
        await session.commit()

    listing = FakeMessage(910, bot_message=True)
    await render_reminders(listing, services_for(sessions))
    settings = FakeMessage(911, bot_message=True)
    await command_settings(settings, services_for(sessions))

    labels = button_texts(listing.edits[-1][1])
    assert any("Check my posture" in label for label in labels)
    assert not any(DIARY_REMINDER_INSTRUCTION[:20] in label for label in labels)
    assert "Diary: 22:00" in settings.edits[-1][0]
