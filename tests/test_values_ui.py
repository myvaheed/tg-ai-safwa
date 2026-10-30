"""The Value screens: what a Value is on, and what deleting one takes it off."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from ui_harness import (
    FakeCallback,
    FakeMessage,
    button_texts,
    link_place,
    place_of,
    press,
    services_for,
    tap_link,
)

from safwa.constants import DESCRIPTION_MAX_CHARS
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, toggle_card_value
from safwa.features.checks.telegram import LINKED_CHECKS_LIMIT
from safwa.features.checks.telegram.screens import CHECK_LIST_LIMIT
from safwa.features.checks.use_cases import (
    archive_check,
    create_check,
    resolve_check,
    toggle_check_value,
)
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


async def test_values_prioritizing_toggles_selection_moves_and_follows_pages(sessions):
    """VL-ORDER-027 — tests/brd/values.feature"""
    async with sessions() as session:
        for name in "ABCDEFG":
            await create_value(session, name, "What matters")
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(800, bot_message=True)
    await command_values(message, services)

    async def tap(text):
        await press(services, message, message.edits[-1][1], text)

    await tap("↕️ Prioritize")
    assert button_texts(message.edits[-1][1])[-3:] == ["←", "→", "↩️ Back"]
    assert len(button_texts(message.edits[-1][1])) == 8
    await tap("5. 💎 E")
    assert "✓ 5. 💎 E" in button_texts(message.edits[-1][1])
    assert button_texts(message.edits[-1][1])[-5:] == ["↑", "↓", "←", "→", "↩️ Back"]
    await tap("1. 💎 A")
    assert "✓ 1. 💎 A" in button_texts(message.edits[-1][1])
    assert "5. 💎 E" in button_texts(message.edits[-1][1])
    await tap("✓ 1. 💎 A")
    assert len(button_texts(message.edits[-1][1])) == 8
    await tap("5. 💎 E")
    await tap("→")
    assert "Selected: E" in message.edits[-1][0]
    assert button_texts(message.edits[-1][1])[-5:] == ["↑", "↓", "←", "→", "↩️ Back"]
    await tap("←")
    await tap("↓")
    assert "page 2/2" in message.edits[-1][0]
    assert "✓ 6. 💎 E" in button_texts(message.edits[-1][1])
    await tap("↓")
    await tap("↓")
    assert "✓ 7. 💎 E" in button_texts(message.edits[-1][1])
    await tap("→")
    assert "page 2/2" in message.edits[-1][0]
    await tap("↩️ Back")
    assert "Prioritization" not in message.edits[-1][0]
    assert "↕️ Prioritize" in button_texts(message.edits[-1][1])
    async with sessions() as session:
        assert [
            value.name for value in await session.scalars(select(Value).order_by(Value.order))
        ] == list("ABCDFGE")


async def test_prioritizing_arrows_keep_the_first_value_and_page_in_bounds(sessions):
    """VL-ORDER-027 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "<Family>", "What matters")
        await session.commit()
        value_id = value.id
    services = services_for(sessions)
    message = FakeMessage(801, bot_message=True)
    await command_values(message, services)
    for text in ("↕️ Prioritize", "1. 💎 <Family>", "↑", "←", "→", "↓"):
        await press(services, message, message.edits[-1][1], text)
    assert "Selected: &lt;Family&gt; · position 1." in message.edits[-1][0]
    assert button_texts(message.edits[-1][1]) == ["✓ 1. 💎 <Family>", "↑", "↓", "←", "→", "↩️ Back"]
    async with sessions() as session:
        assert (await session.get(Value, value_id)).order == 1


async def test_safwa_sets_one_value_order_through_a_reviewed_proposal(sessions):
    """VL-ORDER-028 — tests/brd/values.feature"""
    from safwa.bootstrap.modules import PROPOSALS
    from safwa.features.values.telegram.review import ValueProposalPresenter
    from tg_agent_shell.proposals.prepare import ChangePreparer
    from tg_agent_shell.proposals.store import ProposalStore
    from tg_agent_shell.proposals.use_cases import approve_proposal, prepare_change

    store = ProposalStore()
    async with sessions() as session:
        values = [
            await create_value(session, name, "What matters")
            for name in ("Health", "Freedom", "Growth", "Faith", "Family")
        ]
        await session.commit()
        preparer = ChangePreparer(None, None, PROPOSALS)
        change = await prepare_change(
            session,
            preparer,
            VALUE_TOOL.change_from({"mode": "update", "id": values[-1].id, "order": 2}),
        )
        assert dict(change.values) == {"order": 2}
        screen = await ValueProposalPresenter().screen(session, [change])
        field = next(field for field in screen.fields if field.label == "Order")
        assert (field.before, field.after) == ("5", "2")
        proposal = store.open_proposal(
            workspace_revision=(await preparer.world(session)).revision, changes=[change]
        )
        await approve_proposal(session, store, PROPOSALS, proposal.id)
    async with sessions() as session:
        assert [
            (value.name, value.order)
            for value in await session.scalars(select(Value).order_by(Value.order))
        ] == [("Health", 1), ("Family", 2), ("Freedom", 3), ("Growth", 4), ("Faith", 5)]


async def test_safwa_refuses_invalid_and_stale_value_orders(sessions):
    """VL-ORDER-028 — tests/brd/values.feature"""
    from safwa.bootstrap.modules import PROPOSALS
    from tg_agent_shell.foundation.errors import StaleStateError
    from tg_agent_shell.proposals.prepare import ChangePreparer
    from tg_agent_shell.proposals.store import ProposalStore
    from tg_agent_shell.proposals.use_cases import approve_proposal, prepare_change

    store = ProposalStore()
    async with sessions() as session:
        values = [await create_value(session, name, "What matters") for name in "ABC"]
        await session.commit()
        preparer = ChangePreparer(None, None, PROPOSALS)
        with pytest.raises(DomainError, match="order"):
            await prepare_change(
                session,
                preparer,
                VALUE_TOOL.change_from({"mode": "update", "id": values[0].id, "order": 4}),
            )
        change = await prepare_change(
            session,
            preparer,
            VALUE_TOOL.change_from({"mode": "update", "id": values[-1].id, "order": 1}),
        )
        proposal = store.open_proposal(
            workspace_revision=(await preparer.world(session)).revision, changes=[change]
        )
        await update_value_fields(session, values[0].id, order=3)
        await session.commit()
        with pytest.raises(StaleStateError):
            await approve_proposal(session, store, PROPOSALS, proposal.id)


@pytest.mark.parametrize("order", [0, 1.5, True])
def test_the_value_tool_accepts_only_positive_integer_orders(order):
    """VL-ORDER-028 — tests/brd/values.feature"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ValueToolInput.model_validate({"mode": "update", "id": 1, "order": order})


async def test_the_value_screen_counts_cards_and_checks(sessions) -> None:
    """VL-LINK-004 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health", "What matters to me")
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
    assert "<i>Linked Cards:</i> <a " in text
    linked = await link_place(sessions, text, "⭐️1")
    assert (linked.action, linked.args) == ("value_cards", {"id": value_id})
    assert "<i>Linked Checks:</i>\n• <a " in text
    assert "</a> ✅0 · ❌0" in text
    linked = await link_place(sessions, text, check.title)
    assert (linked.action, linked.args) == ("check_view", {"id": check.id})


async def test_no_screen_sets_a_values_focus_by_hand(sessions) -> None:
    """VL-FOCUS-001 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health", "What matters to me")
        await create_value(session, "Tidiness", "What matters to me")
        await session.commit()
        value_id = value.id

    services = services_for(sessions)
    message = FakeMessage(98, bot_message=True)
    await render_value(message, services, mode="view", item_id=value_id)
    assert not [text for text in button_texts(message.edits[-1][1]) if "Focus" in text]

    listing = FakeMessage(97, bot_message=True)
    await command_values(listing, services)
    assert button_texts(listing.edits[-1][1])[:2] == ["1. 💎 Health", "2. 💎 Tidiness"]


async def test_a_value_description_holds_at_most_description_max_chars(sessions) -> None:
    """VL-DESC-020 — tests/brd/values.feature"""
    too_long = "x" * (DESCRIPTION_MAX_CHARS + 1)
    refusal = (
        f"at most {DESCRIPTION_MAX_CHARS} characters; this one has {DESCRIPTION_MAX_CHARS + 1}"
    )
    async with sessions() as session:
        value = await create_value(session, "Health", "Body first")
        await session.commit()
        value_id = value.id
        before = (value.description, value.version)
        with pytest.raises(DomainError, match=refusal):
            await update_value_fields(session, value_id, description=too_long)
        assert (value.description, value.version) == before
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
        value = Value(name="Connection", order=1)
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
        assert (await link_place(sessions, text, "⭐️1")).action == f"{entity}_cards"
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
        value = await create_value(session, "Health", "What matters to me")
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


async def test_a_value_is_not_created_without_a_description(sessions) -> None:
    """VL-DESC-024 — tests/brd/values.feature"""
    services = services_for(sessions)
    named = FakeMessage(50, bot_message=True)
    await render_value(named, services, mode="create", values={"name": "Health"})
    text, markup = named.edits[-1]
    assert "✅ Create Value" not in button_texts(markup)
    assert "A Value is created once it has a name and a description." in text
    await render_value_text_prompt(named, services, mode="create", item_id=None, field="description")
    assert "why this Value matters to you and what you do for it" in named.bot.edits[-1][1]

    described = FakeMessage(51, bot_message=True)
    await render_value(
        described, services, mode="create",
        values={"name": "Health", "description": "Sleep 7 hours, move every day"},
    )
    assert "✅ Create Value" in button_texts(described.edits[-1][1])

    with pytest.raises(ValueError, match="a new Value needs description"):
        ValueToolInput.model_validate({"mode": "create", "name": "Health"})

    async with sessions() as session:
        with pytest.raises(DomainError, match="A Value needs a description"):
            await create_value(session, "Health", "   ")
        value = await create_value(session, "Health", "Sleep 7 hours, move every day")
        await session.commit()
        with pytest.raises(DomainError, match="A Value needs a description"):
            await update_value_fields(session, value.id, description="")
        await session.rollback()
        assert (await session.get(Value, value.id)).description == "Sleep 7 hours, move every day"


async def test_vl_link_025_a_values_linked_cards_read_as_children_and_open_as_a_list(sessions, effort_on) -> None:
    """VL-LINK-025 — tests/brd/values.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Garden")
        action = await create_card(session, kind="action", title="Water it", effort_points=2)
        item = await create_value(session, "Growth", "What I tend")
        await toggle_card_value(session, goal.id, item.id)
        await toggle_card_value(session, action.id, item.id)
        empty = await create_value(session, "Rest", "What I keep")
        await session.commit()
        item_id, empty_id = item.id, empty.id

    services = services_for(sessions)
    message = FakeMessage(120, bot_message=True)
    await render_value(message, services, mode="view", item_id=item_id)
    text = message.edits[-1][0]
    here = Place("value_view", {"id": item_id})
    linked = "🎯1 · ⭐️1 · ⚡0/2 EP"
    assert await link_place(sessions, text, linked) == here.child("value_cards", id=item_id)

    await tap_link(services, text, linked, message.bot)
    result, markup = message.bot.edits[-1][1:]
    assert "<b>Linked Cards of Growth</b>" in result and "2 matching cards" in result
    assert button_texts(markup)[:2] == ["🎯 Garden · 📚", "⭐️ Water it · 📚"]
    assert await place_of(sessions, markup, "↩️ Back") == here

    await render_value(message, services, mode="view", item_id=empty_id)
    assert "<i>Linked Cards:</i> —" in message.edits[-1][0]


async def test_a_values_linked_checks_open_directly_with_counts_and_return_to_the_value(sessions) -> None:
    """VL-LINK-030 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health & rest", "What matters")
        pending = await create_check(session, title="Slept enough?")
        passed = await create_check(session, title="Feeling rested?")
        archived = await create_check(session, title="No pain?")
        await create_check(session, title="Unrelated?")
        for check in (pending, passed, archived):
            await toggle_check_value(session, check.id, value.id)
        await resolve_check(session, passed.id, "passed")
        await resolve_check(session, archived.id, "missed")
        await archive_check(session, archived.id)
        await session.commit()

    services = services_for(sessions)
    message = FakeMessage(121, bot_message=True)
    here = Place("value_view", {"id": value.id})
    await render_value(message, services, mode="view", item_id=value.id)
    text = message.edits[-1][0]
    for check, label, counts in (
        (pending, "Slept enough?", "✅0 · ❌0"),
        (passed, "Feeling rested?", "✅1 · ❌0"),
        (archived, "No pain? [📦]", "✅0 · ❌1"),
    ):
        assert await link_place(sessions, text, label) == here.child("check_view", id=check.id)
        assert f">{label}</a> {counts}" in text
    assert "Unrelated?" not in text
    assert "Others(" not in text
    await tap_link(services, text, "Slept enough?", message.bot)
    check_text, check_markup = message.bot.edits[-1][1:]
    assert "Slept enough?" in check_text
    assert await place_of(sessions, check_markup, "↩️ Back") == here
    await press(services, message, check_markup, "↩️ Back")
    assert "<i>Name:</i> Health &amp; rest" in message.edits[-1][0]
    async with sessions() as session:
        empty = await create_value(session, "Growth", "What matters")
        await session.commit()
    await render_value(message, services, mode="view", item_id=empty.id)
    assert "<i>Linked Checks:</i>\n—" in message.edits[-1][0]


@pytest.mark.parametrize("count", [LINKED_CHECKS_LIMIT, CHECK_LIST_LIMIT + 1])
async def test_a_value_shows_five_check_links_and_others_opens_all_checks(sessions, count) -> None:
    """VL-LINK-030 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health", "What matters")
        for index in range(count):
            check = await create_check(session, title=f"Observation {index}?")
            await toggle_check_value(session, check.id, value.id)
        await create_check(session, title="Unrelated?")
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(122, bot_message=True)
    await render_value(message, services, mode="view", item_id=value.id)
    text = message.edits[-1][0]
    assert text.count("</a> ✅0 · ❌0") == LINKED_CHECKS_LIMIT
    assert f"Observation {LINKED_CHECKS_LIMIT}?" not in text
    assert text.count("\n• ") == LINKED_CHECKS_LIMIT + (count > LINKED_CHECKS_LIMIT)
    if count == LINKED_CHECKS_LIMIT:
        assert "Others(" not in text
        return
    others = f"Others({count - LINKED_CHECKS_LIMIT})"
    assert text.endswith(f">{others}</a>")
    here = Place("value_view", {"id": value.id})
    listing = here.child("value_checks", id=value.id)
    assert await link_place(sessions, text, others) == listing
    await tap_link(services, text, others, message.bot)
    result, markup = message.bot.edits[-1][1:]
    assert f"{count} matching checks" in result
    assert "Unrelated?" not in result
    assert "⬜ Observation 0?" in button_texts(markup)
    assert await place_of(sessions, markup, "↩️ Back") == here
    await press(services, message, markup, "→")
    result, markup = message.edits[-1]
    assert "page 2/2" in result and "→" not in button_texts(markup)
    label = f"⬜ Observation {count - 1}?"
    assert label in button_texts(markup)
    await press(services, message, markup, label)
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert "page 2/2" in message.edits[-1][0]
    markup = message.edits[-1][1]
    await press(services, message, markup, "↻ Refresh")
    assert f"{count} matching checks" in message.edits[-1][0]
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert "<i>Name:</i> Health" in message.edits[-1][0]
