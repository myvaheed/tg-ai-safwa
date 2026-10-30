"""The Tag screens: the editor, and what a Tag is on."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from ui_harness import (
    CALLBACK_ACTIONS,
    FakeMessage,
    button_texts,
    link_place,
    place_of,
    services_for,
    tap_link,
)

from safwa.constants import DESCRIPTION_MAX_CHARS
from safwa.features.cards.use_cases import create_card, toggle_card_tag
from safwa.features.tags.agent import TAG_TOOL, TagToolInput
from safwa.features.tags.model import Tag
from safwa.features.tags.proposal import TagProposalHandler
from safwa.features.tags.telegram import render_tag
from safwa.features.tags.telegram.screens import render_tag_text_prompt
from safwa.features.tags.use_cases import create_tag, update_tag_fields
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.proposals.api import PreparationContext
from tg_agent_shell.telegram import Place
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.model import UiSession


async def test_tag_field_input_reuses_editor_message_and_deletes_input(sessions) -> None:
    """SC-INPUT-008 — tests/brd/tg_agent_shell/screens.feature"""
    services = services_for(sessions)
    callback = FakeMessage(30, bot_message=True)
    await render_tag(callback, services, mode="create")
    await render_tag_text_prompt(callback, services, mode="create", item_id=None, field="name")
    prompt_id, prompt_text, prompt_markup = callback.bot.edits[-1]
    assert prompt_id == 30
    assert "<i>Current value:</i>\n<pre>—</pre>" in prompt_text
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
    assert "<i>Name:</i> Family" in callback.bot.edits[-1][1]
    async with sessions() as session:
        ui = await session.scalar(select(UiSession).where(UiSession.owner_id == 42))
        assert ui.kind == "item_editor"
        assert ui.state["values"]["name"] == "Family"
        classified = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == 32)
        )
        assert classified.kind == MessageKind.UI_INPUT.value


async def test_a_tag_description_holds_at_most_description_max_chars(sessions) -> None:
    """TA-DESC-011 — tests/brd/tags.feature"""
    too_long = "x" * (DESCRIPTION_MAX_CHARS + 1)
    refusal = (
        f"at most {DESCRIPTION_MAX_CHARS} characters; this one has {DESCRIPTION_MAX_CHARS + 1}"
    )
    async with sessions() as session:
        tag = await create_tag(session, "Family", "People at home")
        await session.commit()
        tag_id = tag.id
        before = (tag.description, tag.version)
        with pytest.raises(DomainError, match=refusal):
            await update_tag_fields(session, tag_id, description=too_long)
        assert (tag.description, tag.version) == before
        with pytest.raises(DomainError, match=refusal):
            await create_tag(session, "Work", too_long)
        await session.rollback()
        assert (await session.get(Tag, tag_id)).description == "People at home"
        await create_tag(session, "Work", "x" * DESCRIPTION_MAX_CHARS)

    # Safwa's call over the limit comes back to it before any screen.
    assert str(DESCRIPTION_MAX_CHARS) in TagToolInput.model_fields["description"].description
    change = TAG_TOOL.change_from({"mode": "update", "id": tag_id, "description": too_long})
    async with sessions() as session:
        context = PreparationContext(
            session=session, world=None, provider=None, query_runner=None, views=frozenset()
        )
        with pytest.raises(DomainError, match=refusal):
            await TagProposalHandler().prepare(context, change)

    services = services_for(sessions)
    screen = FakeMessage(50, bot_message=True)
    await render_tag(screen, services, mode="create")
    await render_tag_text_prompt(screen, services, mode="create", item_id=None, field="description")
    assert f"at most {DESCRIPTION_MAX_CHARS} characters" in screen.bot.edits[-1][1]
    typed = FakeMessage(51, text=too_long, bot_message=False, bot=screen.bot)
    await ordinary_text(typed, services)

    assert refusal in screen.bot.edits[-1][1]
    async with sessions() as session:
        assert (await session.scalar(select(UiSession))).kind == "text_input"


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
    linked = await link_place(sessions, text, "⭐️3")
    assert (linked.action, linked.args) == ("tag_cards", {"id": tag_id})
    # A Tag has no focus, and it never goes on a Check.
    assert "Linked Checks" not in text
    assert not [name for name in button_texts(markup) if "Focus" in name]
    assert "check_choose_tags" not in CALLBACK_ACTIONS

    async with sessions() as session:
        await toggle_card_tag(session, card_id, tag_id)
        await session.commit()
    message = FakeMessage(98, bot_message=True)
    await render_tag(message, services, mode="view", item_id=tag_id)
    assert ">⭐️2</a>" in message.edits[-1][0]


async def test_an_inbox_named_tag_screen_offers_rename_and_delete(sessions) -> None:
    """TA-INBOX-010 — tests/brd/tags.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Inbox")
        await session.commit()
        tag_id = tag.id
    message = FakeMessage(99, bot_message=True)
    await render_tag(message, services_for(sessions), mode="view", item_id=tag_id)
    text, markup = message.edits[-1]
    assert "cannot be renamed or deleted" not in text
    assert "✏️ Name" in button_texts(markup) and "Delete Tag" in button_texts(markup)


async def test_ta_link_012_a_tags_linked_cards_read_as_children_and_open_as_a_list(sessions, effort_on) -> None:
    """TA-LINK-012 — tests/brd/tags.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Garden")
        action = await create_card(session, kind="action", title="Water it", effort_points=2)
        item = await create_tag(session, "garden")
        await toggle_card_tag(session, goal.id, item.id)
        await toggle_card_tag(session, action.id, item.id)
        empty = await create_tag(session, "unused")
        await session.commit()
        item_id, empty_id = item.id, empty.id

    services = services_for(sessions)
    message = FakeMessage(120, bot_message=True)
    await render_tag(message, services, mode="view", item_id=item_id)
    text = message.edits[-1][0]
    here = Place("tag_view", {"id": item_id})
    linked = "🎯1 · ⭐️1 · ⚡0/2 EP"
    assert await link_place(sessions, text, linked) == here.child("tag_cards", id=item_id)

    await tap_link(services, text, linked, message.bot)
    result, markup = message.bot.edits[-1][1:]
    assert "<b>Linked Cards of garden</b>" in result and "2 matching cards" in result
    assert button_texts(markup)[:2] == ["🎯 Garden · 📚", "⭐️ Water it · 📚"]
    assert await place_of(sessions, markup, "↩️ Back") == here

    await render_tag(message, services, mode="view", item_id=empty_id)
    assert "<i>Linked Cards:</i> —" in message.edits[-1][0]
