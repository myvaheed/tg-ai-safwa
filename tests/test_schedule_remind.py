"""Remind: a Schedule with a clock makes a Reminder that follows its Card or Check."""

from __future__ import annotations

from datetime import time, timedelta
from zoneinfo import ZoneInfo

from schedule_helpers import rule_for
from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.features.cards.model import Card
from safwa.features.cards.telegram import render_card
from safwa.features.cards.use_cases import (
    create_card,
    delete_one_card,
    edit_card_schedule,
    edit_card_text,
    finish_action,
    finish_card,
)
from safwa.features.checks.telegram import render_check
from safwa.features.checks.use_cases import create_check, delete_check, resolve_check
from safwa.features.reminders.firing import tick
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.use_cases import delete_reminder
from safwa.features.schedules.api import REMIND_TEXT, entity_reminder
from safwa.features.schedules.use_cases import set_remind
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.telegram import callback_token_handler

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
        "quiet_windows": [],
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
        appointment = action.period_start
        await session.commit()
    services = services_for(sessions)

    message = FakeMessage(70, bot_message=True)
    await render_card(message, services, ids["action"], full=True)
    await _press(message, services, "⏱ Schedule")
    text, markup = message.edits[-1]
    assert "<b>Action Schedule</b>" in text and "Every day at 20:00." in text
    assert button_texts(markup) == ["🔔 Remind: Off", "✏️ Edit", "↩️ Back"]

    await _press(message, services, "🔔 Remind: Off")
    [reminder] = await _reminders(sessions)
    assert reminder.instruction == REMIND_TEXT.format(label="Action", id=ids["action"], title="Stretch")
    assert (reminder.item_type, reminder.item_id) == ("card", ids["action"])
    assert (reminder.schedule_kind, reminder.at_time) == ("daily", time(20, 0))
    assert reminder.next_fire_at == appointment
    assert button_texts(message.edits[-1][1])[0] == "🔔 Remind: On"
    await _press(message, services, "↩️ Back")
    await _press(message, services, "⏱ Schedule")
    await _press(message, services, "🔔 Remind: On")
    assert await _reminders(sessions) == []
    assert button_texts(message.edits[-1][1])[0] == "🔔 Remind: Off"
    await _press(message, services, "✏️ Edit")
    assert "Edit Card Schedule" in message.bot.edits[-1][1]

    goal_message = FakeMessage(80, bot_message=True)
    await render_card(goal_message, services, ids["goal"], full=True)
    await _press(goal_message, services, "⏰ Deadline")
    assert "<b>Goal Deadline</b>" in goal_message.edits[-1][0]
    await _press(goal_message, services, "🔔 Remind: Off")
    [deadline] = await _reminders(sessions)
    assert deadline.schedule_kind == "once"
    assert deadline.next_fire_at.astimezone(TZ).strftime("%Y-%m-%d %H:%M") == "2099-10-20 18:00"

    check_message = FakeMessage(90, bot_message=True)
    await render_check(check_message, services, ids["check"])
    await _press(check_message, services, "⏱ Schedule")
    assert "<b>Check Schedule</b>" in check_message.edits[-1][0]
    await _press(check_message, services, "🔔 Remind: Off")
    assert {(item.item_type, item.item_id) for item in await _reminders(sessions)} == {
        ("card", ids["goal"]),
        ("check", ids["check"]),
    }

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
        await set_remind(session, card, True)
        result = await finish_action(session, card.id)
        successor = await session.get(Card, result.successor_ids[0])
        assert await entity_reminder(session, card) is None
        reminder = await entity_reminder(session, successor)
        assert successor.period_start == card.period_start + timedelta(days=1)
        assert reminder.next_fire_at == successor.period_start
        assert reminder.instruction == REMIND_TEXT.format(
            label="Action", id=successor.id, title="Stretch"
        )
        due, reminder_id = reminder.next_fire_at, reminder.id
        await session.commit()

    assert await tick(sessions, tz=TZ, now=due) is True
    async with sessions() as session:
        [cue] = list(await session.scalars(select(Cue)))
        assert f"Text: Remind is on for Action #{successor.id}" in cue.text
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
        await set_remind(session, card, True)
        reminder = await entity_reminder(session, card)

        await edit_card_schedule(session, card.id, "daily 07:30", await rule_for(session, "daily 07:30"))
        assert reminder.at_time == time(7, 30)
        assert reminder.next_fire_at == card.period_start

        await edit_card_text(session, card.id, "title", "Stretch well")
        assert "«Stretch well»" in reminder.instruction

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
        for item in (once, goal, deleted, check):
            await set_remind(session, item, True)

        await finish_action(session, once.id)
        await finish_card(session, goal.id)
        await delete_one_card(session, deleted.id)
        _answered, next_check = await resolve_check(session, check.id, "passed")
        assert [(item.item_type, item.item_id) for item in await session.scalars(select(Reminder))] == [
            ("check", next_check.id)
        ]

        await delete_check(session, next_check.id)
        assert list(await session.scalars(select(Reminder))) == []

        kept = await create_check(
            session, title="Water?", schedule="daily 10:00", schedule_rule=await rule_for(session, "daily 10:00")
        )
        await set_remind(session, kept, True)
        await delete_reminder(session, (await entity_reminder(session, kept)).id)
        assert await entity_reminder(session, kept) is None
