"""The Tag screens: the editor, and what a Tag is on."""

from __future__ import annotations

from sqlalchemy import select
from ui_harness import CALLBACK_ACTIONS, FakeMessage, button_texts, services_for

from safwa.adapters.kinds import MessageKind
from safwa.adapters.telegram_history import TelegramMessage
from safwa.features.cards.use_cases import create_card, toggle_card_tag
from safwa.features.tags.telegram import render_tag
from safwa.features.tags.telegram.screens import render_tag_text_prompt
from safwa.features.tags.use_cases import create_tag
from safwa.shell.model import UiSession
from safwa.turn.dialogue import ordinary_text


async def test_tag_field_input_reuses_editor_message_and_deletes_input(sessions) -> None:
    services = services_for(sessions)
    callback = FakeMessage(30, bot_message=True)
    await render_tag(callback, services, mode="create")
    await render_tag_text_prompt(callback, services, mode="create", item_id=None, field="name")
    prompt_id, prompt_text, prompt_markup = callback.bot.edits[-1]
    assert prompt_id == 30
    assert "Current value:\n<pre>—</pre>" in prompt_text
    assert button_texts(prompt_markup) == ["↩️ Back"]

    invalid_input = FakeMessage(
        31,
        text="   ",
        bot_message=False,
        bot=callback.bot,
    )
    await ordinary_text(invalid_input, services)

    assert invalid_input.was_deleted is True
    assert "Tag name cannot be empty" in callback.bot.edits[-1][1]
    async with sessions() as session:
        assert (await session.scalar(select(UiSession))).kind == "text_input"

    user_input = FakeMessage(
        32,
        text="Family",
        bot_message=False,
        bot=callback.bot,
    )
    await ordinary_text(user_input, services)

    assert user_input.was_deleted is True
    assert user_input.answers == []
    assert callback.bot.edits[-1][0] == 30
    assert "Name: Family" in callback.bot.edits[-1][1]
    async with sessions() as session:
        ui = await session.scalar(select(UiSession).where(UiSession.owner_id == 42))
        assert ui.kind == "item_editor"
        assert ui.state["values"]["name"] == "Family"
        classified = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == 32)
        )
        assert classified.kind == MessageKind.UI_INPUT.value


async def test_the_tag_screen_counts_its_cards_and_has_no_focus(sessions) -> None:
    """TA-LINK-001 — tests/brd/tags.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Family")
        for title in ("Phone call", "Trip plan", "Birthday"):
            card = await create_card(session, kind="action", title=title, effort_points=1)
            await toggle_card_tag(session, card.id, tag.id)
        await session.commit()
        tag_id, card_id = tag.id, card.id

    services = services_for(sessions)
    message = FakeMessage(97, bot_message=True)
    await render_tag(message, services, mode="view", item_id=tag_id)
    text, markup = message.edits[-1]
    assert "Linked Cards: 3" in text
    # A Tag has no focus, and it never goes on a Check.
    assert "Linked Checks" not in text
    assert not [name for name in button_texts(markup) if "Focus" in name]
    assert [name for name in CALLBACK_ACTIONS if name.startswith("check_choose")] == [
        "check_choose_values"
    ]

    async with sessions() as session:
        await toggle_card_tag(session, card_id, tag_id)
        await session.commit()
    message = FakeMessage(98, bot_message=True)
    await render_tag(message, services, mode="view", item_id=tag_id)
    assert "Linked Cards: 2" in message.edits[-1][0]
