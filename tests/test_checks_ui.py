"""The Check screens: what an archived Check still shows, and how one is deleted."""

from __future__ import annotations

from schedule_helpers import create_card, create_check, with_compiler
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import toggle_card_check
from safwa.features.checks.model import Check, CheckOutcome
from safwa.features.checks.telegram import render_check
from safwa.features.checks.use_cases import (
    archive_check,
    resolve_check,
    toggle_check_value,
)
from safwa.features.values.model import Value
from safwa.features.values.use_cases import create_value
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text


async def test_an_independent_check_schedule_is_compiled_in_its_text_editor(sessions):
    """CH-WRITE-002 — tests/brd/checks.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Posture?")
        check_id = check.id
        await session.commit()
    services = services_for(sessions)
    compiler = with_compiler(
        services, {"five times a day": ({"kind": "quota", "period": "day", "count": 5}, None)}
    )
    message = FakeMessage(590, bot_message=True)
    quota = {"kind": "quota", "period": "day", "count": 5}
    for index, (text, expected, rule) in enumerate(
        [
            ("five times a day", "five times a day", quota),
            ("sometimes", "five times a day", quota),
            ("off", None, None),
        ]
    ):
        await render_check(message, services, check_id)
        button = next(
            button
            for row in message.edits[-1][1].inline_keyboard
            for button in row
            if button.text == "⏱ Schedule"
        )
        await callback_token_handler(
            FakeCallback(button.callback_data.split(":", 1)[1], message), services
        )
        typed = FakeMessage(591 + index, text=text, bot_message=False, bot=message.bot)
        await ordinary_text(typed, services)
        assert typed.was_deleted
        async with sessions() as session:
            check = await session.get(Check, check_id)
            assert check.schedule == expected
            assert (check.schedule_record.rule if check.schedule_record else None) == rule
        if text == "five times a day":
            assert "0/5 answered for the day from" in message.bot.edits[-1][1]
        if text == "sometimes":
            # The question stands in for the value: the same editor asks it and keeps waiting.
            assert "When does sometimes happen?" in message.bot.edits[-1][1]
    assert [target for _, target in compiler.calls] == ["check", "check"]


async def test_ch_archive_016_an_archived_check_keeps_its_answer(sessions) -> None:
    """CH-ARCHIVE-016 — tests/brd/checks.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        await resolve_check(session, check.id, CheckOutcome.PASSED)
        await archive_check(session, check.id)
        await session.commit()
        check_id = check.id

    services = services_for(sessions)
    message = FakeMessage(600, bot_message=True)
    await render_check(message, services, check_id)
    text, labels = message.edits[-1][0], button_texts(message.edits[-1][1])

    assert "[📦]" in text
    assert "Status: ✅" in text
    assert not [label for label in labels if "Passed" in label or "Missed" in label]
    assert not [label for label in labels if "Repeat" in label or "Values" in label]
    # The one thing it still offers is the way to the open instance of its series.
    assert [label for label in labels if label.startswith("🔄 Current:")]


async def test_ch_delete_014_the_owner_deletes_a_check_from_its_screen(sessions) -> None:
    """CH-DELETE-014 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Go to the market", effort_points=2, stage="today"
        )
        value = await create_value(session, "Health")
        check = await create_check(session, title="Milk")
        await toggle_card_check(session, card.id, check.id)
        await toggle_check_value(session, check.id, value.id)
        await session.commit()
        card_id, check_id, value_id = card.id, check.id, value.id

    services = services_for(sessions)
    message = FakeMessage(700, bot_message=True)
    await render_check(message, services, check_id, card_id=card_id)
    remove = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == "🗑 Delete"
    )
    await callback_token_handler(
        FakeCallback(remove.callback_data.split(":", 1)[1], message), services
    )
    confirm = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == "Permanently delete Check"
    )
    await callback_token_handler(
        FakeCallback(confirm.callback_data.split(":", 1)[1], message), services
    )

    async with sessions() as session:
        assert await session.get(Check, check_id) is None
        # Its Card stays, and so does the Value it pointed at.
        assert await session.get(Card, card_id) is not None
        assert await session.get(Value, value_id) is not None
