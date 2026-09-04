"""The Value screens: what a Value is on, and what deleting one takes it off."""

from __future__ import annotations

from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, toggle_card_value
from safwa.features.checks.use_cases import create_check, toggle_check_value
from safwa.features.tags.model import CardTag, Tag
from safwa.features.tags.telegram import render_tag
from safwa.features.values.model import CardValue, Value
from safwa.features.values.telegram import render_value
from safwa.features.values.use_cases import create_value
from tg_agent_shell.telegram import callback_token_handler


async def test_the_value_screen_counts_cards_and_checks_and_flips_focus(sessions) -> None:
    """VL-LINK-004 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        card = await create_card(session, kind="action", title="Morning run", effort_points=1)
        check = await create_check(session, title="Did I sleep seven hours?")
        await toggle_card_value(session, card.id, value.id)
        await toggle_check_value(session, check.id, value.id)
        await session.commit()
        value_id = value.id

    services = services_for(sessions)
    message = FakeMessage(99, bot_message=True)
    await render_value(message, services, mode="view", item_id=value_id)
    text, markup = message.edits[-1]
    assert "Linked Cards: 1" in text
    assert "Linked Checks: 1" in text

    focus = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text.startswith("💎 Focus")
    )
    assert focus.text == "💎 Focus: Off"
    await callback_token_handler(FakeCallback(focus.callback_data.split(":", 1)[1], message), services)
    assert "💎 Focus: On" in button_texts(message.edits[-1][1])


async def test_manual_tag_and_value_delete_unlinks_cards(sessions) -> None:
    """VL-DELETE-015 — tests/brd/values.feature"""
    async with sessions() as session:
        card = Card(kind="action", title="Family walk", effort_points=2)
        tag = Tag(name="Family")
        value = Value(name="Connection", active=True)
        session.add_all([card, tag, value])
        await session.flush()
        session.add_all(
            [
                CardTag(card_id=card.id, tag_id=tag.id),
                CardValue(card_id=card.id, value_id=value.id),
            ]
        )
        await session.commit()
        tag_id, value_id = tag.id, value.id

    services = services_for(sessions)
    for index, (entity, render, item_id) in enumerate(
        (("tag", render_tag, tag_id), ("value", render_value, value_id)), start=70
    ):
        message = FakeMessage(index, bot_message=True)
        await render(message, services, mode="view", item_id=item_id)
        text, markup = message.edits[-1]
        assert "Linked Cards: 1" in text
        assert f"Archive {entity.title()}" not in button_texts(markup)
        remove = next(
            button
            for row in markup.inline_keyboard
            for button in row
            if button.text == f"Delete {entity.title()}"
        )
        await callback_token_handler(
            FakeCallback(remove.callback_data.split(":", 1)[1], message), services
        )
        confirm = next(
            button
            for row in message.edits[-1][1].inline_keyboard
            for button in row
            if button.text == f"Delete {entity.title()}"
        )
        await callback_token_handler(
            FakeCallback(confirm.callback_data.split(":", 1)[1], message), services
        )
        assert "Taken off 1 link(s)" in message.edits[-1][0]

    async with sessions() as session:
        assert await session.get(Tag, tag_id) is None
        assert await session.get(Value, value_id) is None
        assert await session.get(CardTag, {"card_id": card.id, "tag_id": tag_id}) is None
        assert await session.get(CardValue, {"card_id": card.id, "value_id": value_id}) is None
