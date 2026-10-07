"""The Check screens: what an archived Check still shows, and how one is deleted."""

from __future__ import annotations

from schedule_helpers import create_card, create_check, with_compiler
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.features.cards.model import Card
from safwa.features.cards.telegram import render_check_resolution
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
from tg_agent_shell.telegram import Place, callback_token_handler
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
    assert not [label for label in labels if label in {"✅ Yes", "❌ No", "• ✅ Yes"}]
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
    on_card = Place("check_list", {"card_id": card_id}, Place("card_view", {"id": card_id}))
    await render_check(message, services, check_id, back=on_card)
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

    # Back to the list the Check was opened from.
    assert "No Checks yet." in message.edits[-1][0]
    async with sessions() as session:
        assert await session.get(Check, check_id) is None
        # Its Card stays, and so does the Value it pointed at.
        assert await session.get(Card, card_id) is not None
        assert await session.get(Value, value_id) is not None


def _button(message: FakeMessage, text: str):
    return next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == text
    )


async def test_ch_gate_007_answer_buttons_read_yes_and_no(sessions) -> None:
    """CH-GATE-007 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Go to the market", effort_points=2, stage="today"
        )
        milk = await create_check(session, title="Milk")
        bread = await create_check(session, title="Bread")
        await toggle_card_check(session, card.id, milk.id)
        await session.commit()
        card_id, milk_id, bread_id = card.id, milk.id, bread.id

    services = services_for(sessions)
    message = FakeMessage(800, bot_message=True)
    await render_check(
        message,
        services,
        milk_id,
        back=Place("check_list", {"card_id": card_id}, Place("card_view", {"id": card_id})),
    )
    assert {"✅ Yes", "❌ No"} <= set(button_texts(message.edits[-1][1]))

    # One Check needs no number; the buttons carry the answer, never the title.
    back = Place("card_view", {"id": card_id})
    await render_check_resolution(message, services, card_id, back=back)
    labels = button_texts(message.edits[-1][1])
    assert {"✅ Yes", "❌ No"} <= set(labels)
    assert not [label for label in labels if "Milk" in label]

    async with sessions() as session:
        await toggle_card_check(session, card_id, bread_id)
        await session.commit()
    await render_check_resolution(message, services, card_id, back=back)
    text, labels = message.edits[-1][0], button_texts(message.edits[-1][1])
    assert "1. ⬜ Milk — Pending" in text and "2. ⬜ Bread — Pending" in text
    assert {"1. ✅ Yes", "1. ❌ No", "2. ✅ Yes", "2. ❌ No"} <= set(labels)

    await callback_token_handler(
        FakeCallback(_button(message, "2. ❌ No").callback_data.split(":", 1)[1], message),
        services,
    )
    text, labels = message.edits[-1][0], button_texts(message.edits[-1][1])
    assert "2. ❌ Bread — Missed" in text
    assert "2. • ❌ No" in labels


async def test_ch_delete_014_a_check_on_no_card_is_deleted_from_its_screen(sessions) -> None:
    """CH-DELETE-014 — tests/brd/checks.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Posture straight?")
        await session.commit()
        check_id = check.id

    services = services_for(sessions)
    message = FakeMessage(900, bot_message=True)
    # Opened from a citation: no Card, and nowhere particular to go back to.
    await render_check(message, services, check_id)
    await callback_token_handler(
        FakeCallback(_button(message, "🗑 Delete").callback_data.split(":", 1)[1], message),
        services,
    )
    await callback_token_handler(
        FakeCallback(
            _button(message, "Permanently delete Check").callback_data.split(":", 1)[1], message
        ),
        services,
    )

    async with sessions() as session:
        assert await session.get(Check, check_id) is None
    # Back leads home, as it would from the Check itself.
    assert message.edits[-1][0].startswith("<b>🏠 ")
