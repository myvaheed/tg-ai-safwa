"""The closing screen "✅ Done" opens (CD-DONE-071), and the page on what Effort Points mean
(CD-EFFORT-073)."""

from __future__ import annotations

from typing import Any

from schedule_helpers import create_card, create_check
from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.bootstrap.modules import REGISTRY
from safwa.features.cards.api import EFFORT_POINTS_ABOUT
from safwa.features.cards.hooks import EFFORT_POINTS_LINK
from safwa.features.cards.model import EFFORT_RUNGS, Card, effort_label
from safwa.features.cards.telegram import render_card, render_card_choices
from safwa.features.cards.telegram.effort_page import EFFORT_POINTS_TEXT, open_effort_points_page
from safwa.features.cards.use_cases import toggle_card_check
from safwa.features.checks.model import Check, CheckOutcome
from safwa.features.profile.model import ProfileField
from safwa.features.profile.telegram import command_profile
from safwa.features.profile.use_cases import set_profile_field
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text


def _rung(points: float) -> str:
    return f"{effort_label(points)} · {EFFORT_RUNGS[points]}"


def _newest(message: FakeMessage, drawn: int) -> tuple[str, Any]:
    """The screen drawn last: through the Bot API when it drew more than `drawn`, else in
    place."""
    if len(message.bot.edits) > drawn:
        return message.bot.edits[-1][1:]
    return message.edits[-1]


async def _press(message: FakeMessage, services, markup, label: str) -> tuple[str, Any]:
    button = next(item for row in markup.inline_keyboard for item in row if item.text == label)
    drawn = len(message.bot.edits)
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )
    return _newest(message, drawn)


async def _type(message: FakeMessage, services, markup, label: str, text: str) -> tuple[str, Any]:
    """Press the button that opens an editor, then type one value into it."""
    await _press(message, services, markup, label)
    drawn = len(message.bot.edits)
    typed = FakeMessage(message.message_id + 1, text=text, bot_message=False, bot=message.bot)
    await ordinary_text(typed, services)
    return _newest(message, drawn)


async def _switch_on(session, *fields: ProfileField) -> None:
    for field in fields:
        await set_profile_field(session, field, True)


async def _done(message: FakeMessage, services, card_id: int) -> tuple[str, Any]:
    await render_card(message, services, card_id)
    return await _press(message, services, message.edits[-1][1], "✅ Done")


async def test_cd_done_071_done_opens_the_closing_screen_and_save_writes_what_was_set(sessions):
    """CD-DONE-071 — tests/brd/cards.feature"""
    async with sessions() as session:
        await _switch_on(session, ProfileField.TIME_TRACKING, ProfileField.EFFORT_TRACKING)
        card = await create_card(
            session, kind="action", title="Go to the market", effort_points=3, stage="today"
        )
        milk = await create_check(session, title="Milk")
        bread = await create_check(session, title="Bread")
        await toggle_card_check(session, card.id, milk.id)
        await toggle_card_check(session, card.id, bread.id)
        await session.commit()
        card_id, milk_id, bread_id = card.id, milk.id, bread.id
    services = services_for(sessions)
    message = FakeMessage(500, bot_message=True)

    text, markup = await _done(message, services, card_id)
    assert text.startswith("<b>Done — Go to the market</b>")
    assert "Nothing is written until Save." in text
    labels = button_texts(markup)
    assert "⌛ Time spent: —" in labels and "🔢 Effort: 3" in labels
    assert "✅ Save" not in labels

    text, markup = await _press(message, services, markup, "1. ✅ Yes")
    text, markup = await _press(message, services, markup, "✅ Save")
    assert "Answer every Check before saving." in text

    _text, markup = await _press(message, services, markup, "🔢 Effort: 3")
    choices = button_texts(markup)
    assert f"✓ {_rung(3)}" in choices
    assert choices[-2:] == [EFFORT_POINTS_ABOUT, "↩️ Back"]
    page, page_markup = await _press(message, services, markup, EFFORT_POINTS_ABOUT)
    assert page == EFFORT_POINTS_TEXT
    _text, markup = await _press(message, services, page_markup, "↩️ Back")
    assert button_texts(markup) == choices
    _text, markup = await _press(message, services, markup, _rung(5))
    assert {"🔢 Effort: 5", "1. • ✅ Yes"} <= set(button_texts(markup))

    _text, markup = await _type(message, services, markup, "⌛ Time spent: —", "45")
    assert {"⌛ Time spent: 45m", "🔢 Effort: 5", "1. • ✅ Yes"} <= set(button_texts(markup))
    async with sessions() as session:
        unchanged = await session.get(Card, card_id)
        assert (unchanged.effective_stage, unchanged.effort_points) == ("today", 3)

    _text, markup = await _press(message, services, markup, "2. ❌ No")
    await _press(message, services, markup, "✅ Save")
    async with sessions() as session:
        card = await session.get(Card, card_id)
        assert (card.effective_stage, card.tracked_mins, card.effort_points) == ("done", 45, 5)
        assert (await session.get(Check, milk_id)).outcome == CheckOutcome.PASSED.value
        assert (await session.get(Check, bread_id)).outcome == CheckOutcome.MISSED.value


async def test_cd_done_071_a_repeating_actions_next_copy_keeps_its_estimate(sessions):
    """CD-DONE-071 — tests/brd/cards.feature"""
    async with sessions() as session:
        await _switch_on(session, ProfileField.EFFORT_TRACKING)
        card = await create_card(
            session,
            kind="action",
            title="Stretch",
            effort_points=3,
            stage="today",
            schedule="after completion",
        )
        await session.commit()
        card_id = card.id
    services = services_for(sessions)
    message = FakeMessage(510, bot_message=True)

    _text, markup = await _done(message, services, card_id)
    # Time tracking is off, so only Effort is set here.
    assert not [label for label in button_texts(markup) if label.startswith("⌛")]
    _text, markup = await _press(message, services, markup, "🔢 Effort: 3")
    _text, markup = await _press(message, services, markup, _rung(5))
    await _press(message, services, markup, "✅ Save")

    async with sessions() as session:
        assert (await session.get(Card, card_id)).effort_points == 5
        successor = await session.scalar(
            select(Card).where(Card.repeat_series_id == card_id, Card.completed_at.is_(None))
        )
        assert successor is not None and successor.effort_points == 3


async def test_cd_done_071_back_changes_nothing_and_with_nothing_to_set_done_is_at_once(sessions):
    """CD-DONE-071 — tests/brd/cards.feature"""
    async with sessions() as session:
        await _switch_on(session, ProfileField.EFFORT_TRACKING)
        card = await create_card(
            session, kind="action", title="Read", effort_points=3, stage="today"
        )
        await session.commit()
        card_id = card.id
    services = services_for(sessions)
    message = FakeMessage(520, bot_message=True)

    _text, markup = await _done(message, services, card_id)
    _text, markup = await _press(message, services, markup, "🔢 Effort: 3")
    _text, markup = await _press(message, services, markup, _rung(8))
    text, _markup = await _press(message, services, markup, "↩️ Back")
    assert "The Card is still live" in text
    async with sessions() as session:
        card = await session.get(Card, card_id)
        assert (card.effective_stage, card.effort_points) == ("today", 3)
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, False)
        await session.commit()

    text, _markup = await _done(message, services, card_id)
    assert "Card updated." in text
    async with sessions() as session:
        assert (await session.get(Card, card_id)).effective_stage == "done"


async def test_cd_effort_073_the_page_opens_from_the_profile_and_every_effort_choice(sessions):
    """CD-EFFORT-073 — tests/brd/cards.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Run", stage="today")
        await session.commit()
        card_id = card.id
    services = services_for(sessions)
    message = FakeMessage(530, bot_message=True)

    # Effort Points are off, and the page is there to read before switching them on.
    await command_profile(message, services)
    _text, markup = message.edits[-1]
    assert EFFORT_POINTS_ABOUT in button_texts(markup)
    page, markup = await _press(message, services, markup, EFFORT_POINTS_ABOUT)
    assert page == EFFORT_POINTS_TEXT
    for points, words in EFFORT_RUNGS.items():
        assert f"{effort_label(points)} — {words}." in page
    _text, markup = await _press(message, services, markup, "↩️ Back")
    assert EFFORT_POINTS_ABOUT in button_texts(markup)

    async with sessions() as session:
        await _switch_on(session, ProfileField.EFFORT_TRACKING)
        await session.commit()
    own = FakeMessage(531, bot_message=True)
    await render_card_choices(own, services, "card_choose_effort", card_id)
    _text, markup = own.edits[-1]
    assert button_texts(markup)[-2:] == [EFFORT_POINTS_ABOUT, "↩️ Back"]
    page, markup = await _press(own, services, markup, EFFORT_POINTS_ABOUT)
    assert page == EFFORT_POINTS_TEXT
    _text, markup = await _press(own, services, markup, "↩️ Back")
    assert button_texts(markup)[-2:] == [EFFORT_POINTS_ABOUT, "↩️ Back"]


async def test_cd_effort_073_the_reminder_links_the_page(sessions):
    """CD-EFFORT-073 — tests/brd/cards.feature"""
    cited = REGISTRY.screens.citation.search(EFFORT_POINTS_LINK)
    assert cited is not None and (cited[2], cited[3]) == ("effort_points", "1")

    services = services_for(sessions)
    message = FakeMessage(540, bot_message=True)
    await open_effort_points_page(message, services, 1)
    assert message.edits[-1][0] == EFFORT_POINTS_TEXT
