"""The Reminder screens: the list, one Reminder, its text and its delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.constants import WEEKDAY_NAMES
from safwa.features.cards.use_cases import create_card
from safwa.features.reminders.api import resolve, schedule_payload
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.telegram import render_reminder, render_reminders
from safwa.features.reminders.telegram.review import ReminderProposalPresenter
from safwa.features.reminders.use_cases import create_reminder
from tg_agent_shell.proposals.api import ChangeAction, ProposalChange
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text


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
    assert labels[0] == "Take a walk. · every 2 hours"  # what it is about, then when
    assert labels[1].startswith("Review the launch plan. · every Mon at 08:30")  # soonest first

    detail = FakeMessage(62, bot_message=True)
    await render_reminder(detail, services, soon_id)
    # Opened from a link, nothing stands behind it (SC-BACK-012).
    assert button_texts(detail.edits[-1][1]) == ["✏️ Text", "🗑 Delete", "↩️ Menu"]

    opened = listing.edits[-1][1].inline_keyboard[0][0]
    await callback_token_handler(
        FakeCallback(opened.callback_data.split(":", 1)[1], detail), services
    )
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


async def test_a_reminder_remind_made_is_named_by_its_item(sessions) -> None:
    """RM-UI-023 — tests/brd/reminders.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Pull-ups")
        later = datetime.now(UTC) + timedelta(days=3)
        await create_reminder(
            session,
            instruction=f"Remind is on for Action #{card.id} «Pull-ups». Remind the owner about it.",
            schedule=resolve(
                days=WEEKDAY_NAMES, clock="07:00", day=f"{later:%d.%m.%Y}", now=datetime.now(UTC),
                tz=ZoneInfo("UTC"),
            ),
            tz=ZoneInfo("UTC"),
            item_type="card",
            item_id=card.id,
        )
        await session.commit()
    message = FakeMessage(63, bot_message=True)

    await render_reminders(message, services_for(sessions))

    # Its item's title, and no start date: the start is the item's own Schedule.
    assert button_texts(message.edits[-1][1])[0] == "Pull-ups · every day at 07:00"


async def test_a_reminder_proposal_reads_as_its_words_and_its_times(sessions) -> None:
    """RM-WRITE-008 — tests/brd/reminders.feature"""
    now = datetime.now(UTC)
    schedule = resolve(clock="09:00", days=WEEKDAY_NAMES, now=now, tz=ZoneInfo("UTC"))
    async with sessions() as session:
        created = await ReminderProposalPresenter().screen(
            session,
            [
                ProposalChange(
                    entity="reminder",
                    action=ChangeAction.CREATE,
                    values={
                        "instruction": "Call mum.",
                        "schedule": schedule_payload(schedule),
                        "schedule_text": "every day at 09:00",
                    },
                )
            ],
        )
        existing = await create_reminder(
            session, instruction="Call mum.", schedule=schedule, tz=ZoneInfo("UTC")
        )
        await session.flush()
        edited = await ReminderProposalPresenter().screen(
            session,
            [
                ProposalChange(
                    entity="reminder",
                    action=ChangeAction.UPDATE,
                    entity_id=existing.id,
                    values={"instruction": "Call dad."},
                )
            ],
        )

    assert (created.mode, created.item) == ("Create", "Reminder")
    [block] = created.blocks
    assert "<b>When:</b> every day at 09:00" in block and "<b>First:</b> " in block
    assert block.endswith("Call mum.") and "interval" not in block and "{" not in block
    assert created.diffs == ()
    assert edited.mode == "Edit" and edited.diffs == ("• Words: Call mum. → Call dad.",)
