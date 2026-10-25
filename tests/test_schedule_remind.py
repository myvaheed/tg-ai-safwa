"""Remind: a Schedule with a clock makes a Reminder that follows its Card or Check."""

from __future__ import annotations

from datetime import time, timedelta
from zoneinfo import ZoneInfo

from schedule_helpers import rule_for
from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.constants import REMIND_LEAD_MAX_MINUTES
from safwa.features.cards.model import Card
from safwa.features.cards.telegram import render_card
from safwa.features.cards.use_cases import (
    create_card,
    delete_card,
    edit_card_schedule,
    edit_card_text,
    finish_action,
    finish_card,
    toggle_card_check,
)
from safwa.features.checks.model import Check
from safwa.features.checks.telegram import render_check
from safwa.features.checks.use_cases import create_check, delete_check, resolve_check
from safwa.features.reminders.firing import tick
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.use_cases import delete_reminder
from safwa.features.schedules.api import REMIND_LEAD_RANGE, REMIND_TEXT, entity_reminder
from safwa.features.schedules.use_cases import set_remind
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text

TZ = ZoneInfo("Europe/Istanbul")
QUOTA = {"kind": "quota", "period": "day", "count": 5}
DEADLINE = {"kind": "deadline", "date": "2099-10-20", "time": "18:00"}
PAST_ONCE = {
    "kind": "fixed",
    "timing": {
        "schedule_kind": "once",
        "weekdays": [],
        "at_time": "09:00",
        "anchor_at": "2020-01-01T06:00:00+00:00",
        "interval_minutes": None,
    },
}


async def _press(message: FakeMessage, services, label: str) -> None:
    markup = message.edits[-1][1]
    button = next(item for row in markup.inline_keyboard for item in row if item.text == label)
    await callback_token_handler(FakeCallback(button.callback_data.split(":", 1)[1], message), services)


async def _reminders(sessions) -> list[Reminder]:
    async with sessions() as session:
        return list(await session.scalars(select(Reminder)))


async def test_a_schedule_with_a_clock_offers_remind_beside_edit(sessions) -> None:
    """SCH-REMIND-019 — tests/brd/schedules.feature"""
    async with sessions() as session:
        daily = await rule_for(session, "daily 20:00")
        action = await create_card(
            session, kind="action", title="Stretch", schedule="daily 20:00", schedule_rule=daily
        )
        goal = await create_card(
            session, kind="goal", title="Ship v2", schedule="20.10.2099 18:00", schedule_rule=DEADLINE
        )
        check = await create_check(
            session, title="Posture?", schedule="daily 09:00", schedule_rule=await rule_for(session, "daily 09:00")
        )
        without_clock = [
            await create_card(session, kind="action", title=title, schedule=title, schedule_rule=rule)
            for title, rule in [
                ("five times a day", QUOTA),
                ("every day", {**daily, "all_day": True}),
                ("after completion", {"kind": "after_completion"}),
                ("1 January 2020 at 09:00", PAST_ONCE),
            ]
        ] + [
            await create_card(
                session,
                kind="goal",
                title="by 20 October",
                schedule="by 20 October",
                schedule_rule={**DEADLINE, "time": None},
            )
        ]
        ids = {"action": action.id, "goal": goal.id, "check": check.id}
        without_clock_ids = [card.id for card in without_clock]
        appointment, check_appointment = action.period_start, check.period_start
        await session.commit()
    services = services_for(sessions)

    message = FakeMessage(70, bot_message=True)
    await render_card(message, services, ids["action"], full=True)
    await _press(message, services, "⏱ Schedule")
    text, markup = message.edits[-1]
    assert "<b>Action Schedule</b>" in text and "Every day at 20:00." in text
    assert button_texts(markup) == ["🔔 Remind: Off", "✏️ Edit", "↩️ Back"]

    await _press(message, services, "🔔 Remind: Off")
    assert "<b>Remind</b>" in message.edits[-1][0]
    assert button_texts(message.edits[-1][1]) == [
        "✅ Off",
        "At the time",
        "15 minutes before",
        "1 hour before",
        "1 day before",
        "✏️ Other",
        "↩️ Back",
    ]
    await _press(message, services, "↩️ Back")
    assert button_texts(message.edits[-1][1]) == ["🔔 Remind: Off", "✏️ Edit", "↩️ Back"]
    await _press(message, services, "🔔 Remind: Off")
    await _press(message, services, "15 minutes before")
    [reminder] = await _reminders(sessions)
    assert reminder.remind_text == REMIND_TEXT.format(label="Action", id=ids["action"], title="Stretch")
    assert (reminder.item_type, reminder.item_id) == ("card", ids["action"])
    assert (reminder.schedule_kind, reminder.at_time, reminder.lead_minutes) == ("daily", time(20, 0), 15)
    assert reminder.next_fire_at == appointment - timedelta(minutes=15)
    assert button_texts(message.edits[-1][1])[0] == "🔔 Remind: 15 minutes before"

    await _press(message, services, "🔔 Remind: 15 minutes before")
    assert "✅ 15 minutes before" in button_texts(message.edits[-1][1])
    await _press(message, services, "1 hour before")
    [moved] = await _reminders(sessions)
    assert (moved.id, moved.remind_text, moved.lead_minutes) == (reminder.id, reminder.remind_text, 60)
    assert moved.next_fire_at == appointment - timedelta(hours=1)

    await _press(message, services, "🔔 Remind: 1 hour before")
    await _press(message, services, "Off")
    assert await _reminders(sessions) == []
    assert button_texts(message.edits[-1][1])[0] == "🔔 Remind: Off"
    await _press(message, services, "✏️ Edit")
    assert "Edit Card Schedule" in message.bot.edits[-1][1]

    other = FakeMessage(75, bot_message=True)
    await render_card(other, services, ids["action"], full=True)
    await _press(other, services, "⏱ Schedule")
    await _press(other, services, "🔔 Remind: Off")
    await _press(other, services, "✏️ Other")
    assert "<b>Remind</b>" in other.bot.edits[-1][1]
    for refused in ("soon", str(REMIND_LEAD_MAX_MINUTES + 1)):
        typed = FakeMessage(76, text=refused, bot_message=False, bot=other.bot)
        await ordinary_text(typed, services)
        assert REMIND_LEAD_RANGE in other.bot.edits[-1][1]
        assert await _reminders(sessions) == []
    typed = FakeMessage(77, text="30", bot_message=False, bot=other.bot)
    await ordinary_text(typed, services)
    assert typed.was_deleted
    assert button_texts(other.bot.edits[-1][2])[0] == "🔔 Remind: 30 minutes before"
    assert [item.lead_minutes for item in await _reminders(sessions)] == [30]

    goal_message = FakeMessage(80, bot_message=True)
    await render_card(goal_message, services, ids["goal"], full=True)
    await _press(goal_message, services, "⏰ Deadline")
    assert "<b>Goal Deadline</b>" in goal_message.edits[-1][0]
    await _press(goal_message, services, "🔔 Remind: Off")
    await _press(goal_message, services, "At the time")
    assert button_texts(goal_message.edits[-1][1])[0] == "🔔 Remind: At the time"
    async with sessions() as session:
        deadline = await entity_reminder(session, await session.get(Card, ids["goal"]))
    assert (deadline.schedule_kind, deadline.lead_minutes) == ("once", 0)
    assert deadline.next_fire_at.astimezone(TZ).strftime("%Y-%m-%d %H:%M") == "2099-10-20 18:00"

    check_message = FakeMessage(90, bot_message=True)
    await render_check(check_message, services, ids["check"])
    await _press(check_message, services, "⏱ Schedule")
    assert "<b>Check Schedule</b>" in check_message.edits[-1][0]
    await _press(check_message, services, "🔔 Remind: Off")
    await _press(check_message, services, "1 day before")
    assert {(item.item_type, item.item_id) for item in await _reminders(sessions)} == {
        ("card", ids["action"]),
        ("card", ids["goal"]),
        ("check", ids["check"]),
    }
    async with sessions() as session:
        daily_check = await entity_reminder(session, await session.get(Check, ids["check"]))
    assert daily_check.next_fire_at == check_appointment - timedelta(days=1)

    for position, card_id in enumerate(without_clock_ids):
        plain = FakeMessage(100 + position, bot_message=True)
        await render_card(plain, services, card_id, full=True)
        label = "⏰ Deadline" if position == len(without_clock_ids) - 1 else "⏱ Schedule"
        await _press(plain, services, label)
        assert "Edit Card" in plain.bot.edits[-1][1]


async def test_remind_moves_to_the_next_instance_and_keeps_reminding_while_overdue(sessions) -> None:
    """SCH-REMIND-020 — tests/brd/schedules.feature"""
    async with sessions() as session:
        card = await create_card(
            session,
            kind="action",
            title="Stretch",
            schedule="daily 20:00",
            schedule_rule=await rule_for(session, "daily 20:00"),
        )
        await set_remind(session, card, 15)
        result = await finish_action(session, card.id)
        successor = await session.get(Card, result.successor_ids[0])
        assert await entity_reminder(session, card) is None
        reminder = await entity_reminder(session, successor)
        assert successor.period_start == card.period_start + timedelta(days=1)
        assert reminder.lead_minutes == 15
        assert reminder.next_fire_at == successor.period_start - timedelta(minutes=15)
        assert reminder.remind_text == REMIND_TEXT.format(
            label="Action", id=successor.id, title="Stretch"
        )
        due, reminder_id = reminder.next_fire_at, reminder.id
        await session.commit()

    assert await tick(sessions, tz=TZ, now=due) is True
    async with sessions() as session:
        [cue] = list(await session.scalars(select(Cue)))
        assert f"The user asked to be reminded of: Action #{successor.id}" in cue.text
        assert (await session.get(Reminder, reminder_id)).next_fire_at == due + timedelta(days=1)
        assert (await session.get(Card, successor.id)).completed_at is None


async def test_remind_follows_a_changed_schedule_and_title(sessions) -> None:
    """SCH-REMIND-020 — tests/brd/schedules.feature"""
    async with sessions() as session:
        card = await create_card(
            session,
            kind="action",
            title="Stretch",
            schedule="daily 20:00",
            schedule_rule=await rule_for(session, "daily 20:00"),
        )
        await set_remind(session, card, 15)
        reminder = await entity_reminder(session, card)

        await edit_card_schedule(session, card.id, "daily 07:30", await rule_for(session, "daily 07:30"))
        assert (reminder.at_time, reminder.lead_minutes) == (time(7, 30), 15)
        assert reminder.next_fire_at == card.period_start - timedelta(minutes=15)

        await edit_card_text(session, card.id, "title", "Stretch well")
        assert "«Stretch well»" in reminder.remind_text

        await edit_card_schedule(session, card.id, "five times a day", QUOTA)
        assert await entity_reminder(session, card) is None


async def test_remind_ends_with_its_item(sessions) -> None:
    """SCH-REMIND-020 — tests/brd/schedules.feature"""
    async with sessions() as session:
        once = await create_card(
            session,
            kind="action",
            title="Call",
            schedule="20.10.2099 18:00",
            schedule_rule=await rule_for(session, "20.10.2099 18:00"),
        )
        goal = await create_card(
            session, kind="goal", title="Ship v2", schedule="by 20 October 18:00", schedule_rule=DEADLINE
        )
        deleted = await create_card(
            session,
            kind="action",
            title="Run",
            schedule="daily 07:00",
            schedule_rule=await rule_for(session, "daily 07:00"),
        )
        check = await create_check(
            session, title="Posture?", schedule="daily 09:00", schedule_rule=await rule_for(session, "daily 09:00")
        )
        on_goal = await create_check(
            session, title="Shipped?", schedule="daily 19:00", schedule_rule=await rule_for(session, "daily 19:00")
        )
        await toggle_card_check(session, goal.id, on_goal.id)
        for item in (once, goal, deleted, check, on_goal):
            await set_remind(session, item, 0)

        await finish_action(session, once.id)
        await finish_card(session, goal.id, check_outcomes={on_goal.id: "passed"})
        await delete_card(session, deleted.id)
        _answered, next_check = await resolve_check(session, check.id, "passed")
        assert [(item.item_type, item.item_id) for item in await session.scalars(select(Reminder))] == [
            ("check", next_check.id)
        ]

        await delete_check(session, next_check.id)
        assert list(await session.scalars(select(Reminder))) == []

        kept = await create_check(
            session, title="Water?", schedule="daily 10:00", schedule_rule=await rule_for(session, "daily 10:00")
        )
        await set_remind(session, kept, 0)
        await delete_reminder(session, (await entity_reminder(session, kept)).id)
        assert await entity_reminder(session, kept) is None
