"""The Check screens: what an archived Check still shows, and how one is deleted."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from schedule_helpers import create_card, create_check, with_compiler
from ui_harness import FakeCallback, FakeMessage, button_texts, place_of, press, services_for

from safwa.features.cards.model import Card
from safwa.features.cards.telegram import render_check_resolution
from safwa.features.cards.use_cases import toggle_card_check
from safwa.features.checks.model import Check, CheckOutcome
from safwa.features.checks.telegram import render_check, render_checks
from safwa.features.checks.telegram.screens import CHECK_LIST_LIMIT
from safwa.features.checks.use_cases import (
    archive_check,
    resolve_check,
    toggle_check_value,
)
from safwa.features.values.model import Value
from safwa.features.values.use_cases import create_value
from tg_agent_shell.telegram import Place, callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.layout import PAGE_SIZE


async def test_answered_history_counts_only_its_series_and_orders_by_answer_time(sessions):
    """CH-HISTORY-021 — tests/brd/checks.feature"""
    async with sessions() as session:
        first = await create_check(session, title="Slept & rested?", schedule="after completion")
        _, second = await resolve_check(session, first.id, "passed")
        _, current = await resolve_check(session, second.id, "missed")
        # Resolution order deliberately differs from creation order.
        first.resolved_at = datetime(2026, 10, 18, 12, tzinfo=UTC)
        second.resolved_at = first.resolved_at - timedelta(days=1)
        await archive_check(session, first.id)
        unrelated = await create_check(session, title=first.title)
        await resolve_check(session, unrelated.id, "passed")
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(910, bot_message=True)
    back = Place("value_view", {"id": 99})
    here = Place("check_view", {"id": current.id}, back)
    await render_check(message, services, current.id, back=back)
    await press(services, message, message.edits[-1][1], "Answered: ✅1 · ❌1")
    text, markup = message.edits[-1]
    assert "Slept &amp; rested?" in text and "2 matching checks" in text
    labels = button_texts(markup)
    assert labels[0].startswith("✅ ") and labels[1].startswith("❌ ")
    history = here.child("check_answered", id=current.id)
    assert await place_of(sessions, markup, labels[0]) == history.child("check_view", id=first.id)
    assert await place_of(sessions, markup, labels[1]) == history.child("check_view", id=second.id)
    assert await place_of(sessions, markup, "↩️ Back") == here
    await press(services, message, markup, labels[0])
    assert "[📦]" in message.edits[-1][0]
    assert await place_of(sessions, message.edits[-1][1], "↩️ Back") == history
    await press(services, message, message.edits[-1][1], "↩️ Back")
    async with sessions() as session:
        await resolve_check(session, second.id, "passed")
        await session.commit()
    await press(services, message, message.edits[-1][1], "↻ Refresh")
    assert all(label.startswith("✅ ") for label in button_texts(message.edits[-1][1])[:2])
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert "Answered: ✅2 · ❌0" in button_texts(message.edits[-1][1])


async def test_answered_history_pages_all_answers_without_listing_pending(sessions):
    """CH-HISTORY-021 — tests/brd/checks.feature"""
    async with sessions() as session:
        current = await create_check(session, title="Rested?", schedule="after completion")
        first_id = current.id
        for _ in range(PAGE_SIZE + 1):
            _, current = await resolve_check(session, current.id, "passed")
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(911, bot_message=True)
    await render_check(message, services, current.id)
    await press(services, message, message.edits[-1][1], f"Answered: ✅{PAGE_SIZE + 1} · ❌0")
    text, markup = message.edits[-1]
    assert "page 1/2" in text and "←" not in button_texts(markup)
    assert len([label for label in button_texts(markup) if label.startswith("✅ ")]) == PAGE_SIZE
    await press(services, message, markup, "→")
    text, markup = message.edits[-1]
    assert "page 2/2" in text and "→" not in button_texts(markup)
    label = button_texts(markup)[0]
    assert (await place_of(sessions, markup, label)).args == {"id": first_id}
    await press(services, message, markup, label)
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert "page 2/2" in message.edits[-1][0]


async def test_an_unanswered_single_check_has_no_history_button(sessions):
    """CH-HISTORY-021 — tests/brd/checks.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Rested?")
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(912, bot_message=True)
    await render_check(message, services, check.id)
    assert not any(label.startswith("Answered:") for label in button_texts(message.edits[-1][1]))
    async with sessions() as session:
        await resolve_check(session, check.id, "missed")
        await session.commit()
    await render_check(message, services, check.id)
    await press(services, message, message.edits[-1][1], "Answered: ✅0 · ❌1")
    assert "1 matching check" in message.edits[-1][0]


async def test_a_cards_check_result_reaches_every_attached_check(sessions):
    """VL-LINK-030 — tests/brd/values.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="goal", title="Health")
        for index in range(CHECK_LIST_LIMIT + 1):
            check = await create_check(session, title=f"Observation {index}?")
            await toggle_card_check(session, card.id, check.id)
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(913, bot_message=True)
    back = Place("card_view", {"id": card.id})
    await render_checks(message, services, card.id, back=back)
    assert "page 1/2" in message.edits[-1][0]
    await press(services, message, message.edits[-1][1], "→")
    text, markup = message.edits[-1]
    assert "page 2/2" in text and "→" not in button_texts(markup)
    label = f"⬜ Observation {CHECK_LIST_LIMIT}?"
    assert (await place_of(sessions, markup, label)).args == {"id": check.id}
    await press(services, message, markup, label)
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert "page 2/2" in message.edits[-1][0]


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


async def test_the_schedule_button_shows_on_a_goal_and_never_on_an_action(sessions):
    """CH-WRITE-002 — tests/brd/checks.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Weigh 75 kg")
        action = await create_card(session, kind="action", title="Walk")
        on_goal = await create_check(session, title="Under 80 kg?")
        on_action = await create_check(session, title="Shoes?")
        await toggle_card_check(session, goal.id, on_goal.id)
        await toggle_card_check(session, action.id, on_action.id)
        check_ids = (on_goal.id, on_action.id)
        await session.commit()
    services = services_for(sessions)
    shown = []
    for check_id in check_ids:
        message = FakeMessage(600 + check_id, bot_message=True)
        await render_check(message, services, check_id)
        shown.append("⏱ Schedule" in button_texts(message.edits[-1][1]))
    assert shown == [True, False]


async def test_note_is_edited_from_the_check_screen(sessions):
    """CH-WRITE-018 — tests/brd/checks.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Slept?", note="Original explanation")
        check_id = check.id
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(595, bot_message=True)
    await render_check(message, services, check_id)
    assert "<i>Note:</i>\nOriginal explanation" in message.edits[-1][0]
    button = _button(message, "📝 Note")
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )
    typed = FakeMessage(596, text="Sleep <restores> health", bot_message=False, bot=message.bot)
    await ordinary_text(typed, services)
    assert typed.was_deleted
    async with sessions() as session:
        check = await session.get(Check, check_id)
        assert check.note == "Sleep <restores> health"
        await resolve_check(session, check_id, CheckOutcome.PASSED)
        await archive_check(session, check_id)
        await session.commit()
    assert "Sleep &lt;restores&gt; health" in message.bot.edits[-1][1]
    await render_check(message, services, check_id)
    assert "Sleep &lt;restores&gt; health" in message.edits[-1][0]
    assert "📝 Note" not in button_texts(message.edits[-1][1])


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
    assert "<i>Status:</i> ✅" in text
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
        value = await create_value(session, "Health", "What matters to me")
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
