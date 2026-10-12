"""The Value screens: what a Value is on, and what deleting one takes it off."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, button_texts, place_of, services_for

from safwa.constants import DESCRIPTION_MAX_CHARS
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, toggle_card_value
from safwa.features.checks.use_cases import create_check, toggle_check_value
from safwa.features.tags.model import CardTag, Tag
from safwa.features.tags.telegram import render_tag
from safwa.features.values.agent import VALUE_TOOL, ValueToolInput
from safwa.features.values.model import CardValue, Value
from safwa.features.values.proposal import ValueProposalHandler
from safwa.features.values.telegram import command_values, open_value, render_value
from safwa.features.values.telegram.screens import render_value_text_prompt
from safwa.features.values.use_cases import create_value, update_value_fields
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.proposals.api import PreparationContext
from tg_agent_shell.telegram import Place, callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.model import UiSession


async def test_the_value_screen_counts_cards_and_checks(sessions) -> None:
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


async def test_no_screen_sets_a_values_focus_by_hand(sessions) -> None:
    """VL-FOCUS-001 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        await create_value(session, "Tidiness")
        await session.commit()
        value_id = value.id

    services = services_for(sessions)
    message = FakeMessage(98, bot_message=True)
    await render_value(message, services, mode="view", item_id=value_id)
    assert not [text for text in button_texts(message.edits[-1][1]) if "Focus" in text]

    listing = FakeMessage(97, bot_message=True)
    await command_values(listing, services)
    assert button_texts(listing.edits[-1][1])[:2] == ["💎 Health", "💎 Tidiness"]


async def test_a_value_description_holds_at_most_300_characters(sessions) -> None:
    """VL-DESC-020 — tests/brd/values.feature"""
    too_long = "x" * (DESCRIPTION_MAX_CHARS + 1)
    refusal = (
        f"at most {DESCRIPTION_MAX_CHARS} characters; this one has {DESCRIPTION_MAX_CHARS + 1}"
    )
    async with sessions() as session:
        value = await create_value(session, "Health", "Body first")
        await session.commit()
        value_id = value.id
        with pytest.raises(DomainError, match=refusal):
            await update_value_fields(session, value_id, description=too_long)
        with pytest.raises(DomainError, match=refusal):
            await create_value(session, "Calm", too_long)
        await session.rollback()
        await create_value(session, "Calm", "x" * DESCRIPTION_MAX_CHARS)

    # Safwa's call over the limit comes back to it before any screen.
    assert str(DESCRIPTION_MAX_CHARS) in ValueToolInput.model_fields["description"].description
    change = VALUE_TOOL.change_from({"mode": "update", "id": value_id, "description": too_long})
    async with sessions() as session:
        context = PreparationContext(
            session=session, world=None, provider=None, query_runner=None, views=frozenset()
        )
        with pytest.raises(DomainError, match=refusal):
            await ValueProposalHandler().prepare(context, change)

    services = services_for(sessions)
    screen = FakeMessage(40, bot_message=True)
    await render_value(screen, services, mode="view", item_id=value_id)
    await render_value_text_prompt(
        screen, services, mode="view", item_id=value_id, field="description"
    )
    assert f"at most {DESCRIPTION_MAX_CHARS} characters" in screen.bot.edits[-1][1]
    typed = FakeMessage(41, text=too_long, bot_message=False, bot=screen.bot)
    await ordinary_text(typed, services)

    assert refusal in screen.bot.edits[-1][1]
    async with sessions() as session:
        assert (await session.get(Value, value_id)).description == "Body first"
        assert (await session.scalar(select(UiSession))).kind == "text_input"


async def test_manual_tag_and_value_delete_unlinks_cards(sessions) -> None:
    """VL-DELETE-015 — tests/brd/values.feature"""
    async with sessions() as session:
        card = Card(kind="action", title="Family walk", effort_points=2)
        tag = Tag(name="Family")
        value = Value(name="Connection")
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


async def test_a_value_goes_back_where_it_was_opened_from(sessions) -> None:
    """SC-BACK-012 — tests/brd/tg_agent_shell/screens.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        card = await create_card(session, kind="goal", title="Get fit")
        await toggle_card_value(session, card.id, value.id)
        await session.commit()
        value_id, card_id = value.id, card.id
    services = services_for(sessions)

    # From a link, as on the Home dashboard, nothing stands behind it: not the Values list.
    linked = FakeMessage(120, bot_message=True)
    await open_value(linked, services, value_id)
    assert button_texts(linked.edits[-1][1])[-1] == "↩️ Menu"

    listing = FakeMessage(121, bot_message=True)
    await command_values(listing, services)
    opened = listing.edits[-1][1].inline_keyboard[0][0]
    await callback_token_handler(FakeCallback(opened.callback_data.split(":", 1)[1], listing), services)
    assert await place_of(sessions, listing.edits[-1][1], "↩️ Back") == Place("value_list")

    # From its Card, Back is that Card.
    goal = Place("card_view", {"id": card_id})
    await render_value(listing, services, mode="view", item_id=value_id, back=goal)
    assert await place_of(sessions, listing.edits[-1][1], "↩️ Back") == goal
