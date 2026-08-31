"""The Check screens: what an archived Check still shows, and how one is deleted."""

from __future__ import annotations

from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.domain import (
    archive_check,
    create_card,
    create_check,
    create_value,
    resolve_check,
    toggle_card_check,
    toggle_check_value,
)
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.telegram import render_check
from safwa.models import Card, Check, Value
from safwa.shell import callback_token_handler


async def test_ch_archive_016_an_archived_check_keeps_its_answer(sessions) -> None:
    """CH-ARCHIVE-016 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Posture", effort_points=2, stage="today"
        )
        check = await create_check(session, title="Sat straight?", repeatable=True)
        await toggle_card_check(session, card.id, check.id)
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
