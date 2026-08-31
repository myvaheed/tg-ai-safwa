"""The Sprint screens: Today, Planning, and the plan the owner builds."""

from __future__ import annotations

from sqlalchemy import select
from ui_harness import (
    FakeCallback,
    FakeMessage,
    button_texts,
    plan_filters,
    seed_plan,
    services_for,
)

from safwa.ai.sql import create_ai_views
from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS
from safwa.constants import PLAN_LINK_BURST_TAPS
from safwa.features.cards.model import CardStage
from safwa.features.cards.use_cases import create_card
from safwa.features.planning.telegram import (
    handle_plan_start,
    render_plan,
    render_sprint,
    render_today,
)
from safwa.features.planning.telegram.plan import claims_plan_payload
from safwa.features.planning.use_cases import set_sprint_success_criteria, start_sprint
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.saved_requests.use_cases import create_saved_request
from safwa.foundation.clock import SystemClock
from safwa.models import Card, Cue, Reminder, Sprint, UiSession, Workspace
from safwa.shell import callback_token_handler, command_start
from safwa.turn.dialogue import ordinary_text


async def test_pl_mode_001_the_menu_offers_today_only_while_a_sprint_runs(sessions) -> None:
    """PL-MODE-001 — tests/brd/planning.feature"""
    services = services_for(sessions)
    message = FakeMessage(74, bot_message=True)

    await command_start(message, services)
    assert "☀️ Today" not in button_texts(message.edits[-1][1])

    async with sessions() as session:
        await create_card(
            session, kind="action", title="Planned", stage="sprint", effort_points=2
        )
        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()

    await command_start(message, services)
    assert "☀️ Today" in button_texts(message.edits[-1][1])


async def test_pl_mode_001_the_today_screen_is_closed_during_planning(sessions) -> None:
    """PL-MODE-001 — tests/brd/planning.feature"""
    message = FakeMessage(75, bot_message=True)

    await render_today(message, services_for(sessions))

    text, markup = message.edits[-1]
    assert "Plan the next Sprint first" in text
    assert button_texts(markup) == ["↩️ Menu"]


async def test_quick_move_buttons_walk_an_action_between_today_and_sprint(sessions) -> None:
    async with sessions() as session:
        action = await create_card(
            session, kind="action", title="Ship it", stage="today", effort_points=2
        )
        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
        action_id = action.id

    services = services_for(sessions)
    message = FakeMessage(76, bot_message=True)
    await render_today(message, services)

    text, markup = message.edits[-1]
    assert "Ship it" in text
    move_to_sprint = next(
        button for row in markup.inline_keyboard for button in row if button.text == "🏃"
    )
    assert markup.inline_keyboard[0][0] is move_to_sprint

    await callback_token_handler(
        FakeCallback(move_to_sprint.callback_data.split(":", 1)[1], message), services
    )

    async with sessions() as session:
        assert (await session.get(Card, action_id)).effective_stage == CardStage.SPRINT.value
    text, markup = message.edits[-1]
    assert "Ship it" not in text

    await render_sprint(message, services)
    text, markup = message.edits[-1]
    move_to_today = next(
        button for row in markup.inline_keyboard for button in row if button.text == "☀️"
    )
    assert markup.inline_keyboard[0][-1] is move_to_today

    await callback_token_handler(
        FakeCallback(move_to_today.callback_data.split(":", 1)[1], message), services
    )

    async with sessions() as session:
        assert (await session.get(Card, action_id)).effective_stage == CardStage.TODAY.value


async def test_pl_criteria_003_starting_a_sprint_needs_criteria_and_a_plan(sessions) -> None:
    """PL-CRITERIA-003 — tests/brd/planning.feature"""
    async with sessions() as session:
        await create_card(
            session, kind="action", title="Sprint work", stage="sprint", effort_points=2
        )
        await create_card(
            session, kind="action", title="Today work", stage="today", effort_points=3
        )
        await session.commit()

    services = services_for(sessions)
    message = FakeMessage(77, bot_message=True, answer_as_new=True)
    await render_sprint(message, services)

    text, markup = message.edits[-1]
    assert "Success criteria: not set yet" in text
    assert "Planned: 2 Actions · 5 EP" in text
    # There is nothing to start until the Sprint is told what it is for.
    assert not any(label.startswith("▶️ Start") for label in button_texts(markup))
    criteria = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text == "🎯 Set Success criteria"
    )

    await callback_token_handler(
        FakeCallback(criteria.callback_data.split(":", 1)[1], message), services
    )

    prompt_id, prompt_text, prompt_markup = message.bot.edits[-1]
    assert prompt_id == 77
    assert "Send what this Sprint must achieve" in prompt_text
    assert button_texts(prompt_markup) == ["↩️ Back"]
    async with sessions() as session:
        assert (await session.scalar(select(UiSession))).kind == "text_input"

    invalid = FakeMessage(78, text=" ", bot_message=False, bot=message.bot)
    await ordinary_text(invalid, services)
    assert invalid.was_deleted is True
    assert "Success criteria cannot be empty" in message.bot.edits[-1][1]

    typed = FakeMessage(79, text="Ship v2 to production", bot_message=False, bot=message.bot)
    await ordinary_text(typed, services)

    planning_id, planning_text, planning_markup = message.bot.edits[-1]
    assert planning_id == 77
    assert "Success criteria: Ship v2 to production" in planning_text
    assert "▶️ Start 14-day Sprint" in button_texts(planning_markup)

    start = next(
        button
        for row in planning_markup.inline_keyboard
        for button in row
        if button.text.startswith("▶️ Start")
    )
    await callback_token_handler(
        FakeCallback(start.callback_data.split(":", 1)[1], message), services
    )

    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        assert workspace.active_sprint_id is not None
        sprint = await session.get(Sprint, workspace.active_sprint_id)
        assert sprint.success_criteria == "Ship v2 to production"
        assert len(list(await session.scalars(select(Reminder)))) == 2
    # The Today command becomes available again the moment the Sprint exists.
    assert "today" in message.bot.published_commands[-1]

    finish = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == "⏹ Finish early"
    )
    await callback_token_handler(
        FakeCallback(finish.callback_data.split(":", 1)[1], message), services
    )

    assert "today" not in message.bot.published_commands[-1]
    async with sessions() as session:
        assert (await session.get(Workspace, 1)).active_sprint_id is None
        # Both end warnings are gone, and the Sprint was handed over as a Cue.
        assert list(await session.scalars(select(Reminder))) == []
        handed = list(await session.scalars(select(Cue)))
        assert len(handed) == 1
        assert handed[0].text.startswith("Sprint 1 is over")


async def test_pl_criteria_003_the_planning_screen_refuses_an_empty_plan(sessions) -> None:
    """PL-CRITERIA-003 — tests/brd/planning.feature"""
    async with sessions() as session:
        await set_sprint_success_criteria(session, "Ship v2")
        await session.commit()

    message = FakeMessage(79, bot_message=True)
    await render_sprint(message, services_for(sessions))

    text, markup = message.edits[-1]
    assert "Planned: 0 Actions · 0 EP" in text
    assert not any(label.startswith("▶️ Start") for label in button_texts(markup))
    assert "🗓 Plan" in button_texts(markup)


async def test_pl_plan_016_the_plan_is_a_table_and_the_backlog_is_the_keyboard(sessions):
    """PL-PLAN-016 — tests/brd/planning.feature"""
    ids = await seed_plan(sessions)
    services = services_for(sessions)
    screen = FakeMessage(100, bot_message=True)
    await render_plan(screen, services)

    body, markup = screen.edits[-1]
    # Every planned Action is a row: its title opens it, its Return sends it back.
    assert f"?start=sp-{ids['sprint']}" in body
    assert f"?start=sr-{ids['sprint']}" in body
    assert "<table bordered striped>" in body
    assert "In Sprint: 1 Actions" in body
    labels = button_texts(markup)
    assert "Pick me (1)" in labels
    assert "Skip me (2)" in labels
    assert "Ship it (3)" not in labels
    assert "Apply filter" in " ".join(labels)


async def test_pl_plan_016_a_return_tap_moves_the_card_back_and_redraws_the_same_screen(sessions):
    """PL-PLAN-016 — tests/brd/planning.feature"""
    ids = await seed_plan(sessions)
    services = services_for(sessions)
    screen = FakeMessage(101, bot_message=True)
    await render_plan(screen, services)

    payload = f"sr-{ids['sprint']}"
    tap = FakeMessage(102, text="/start " + payload, bot_message=False, bot=screen.bot)
    assert claims_plan_payload(payload) is True
    await handle_plan_start(tap, services, payload)

    async with sessions() as session:
        assert (await session.get(Card, ids["sprint"])).effective_stage == CardStage.BACKLOG.value
    # The plan took its own place; the tap did not open a screen of its own.
    assert screen.bot.edits[-1][0] == screen.message_id
    assert "Nothing planned yet." in screen.bot.edits[-1][1]


async def test_opening_a_card_from_the_plan_comes_back_to_the_same_page_and_filters(sessions):
    ids = await seed_plan(sessions)
    async with sessions() as session:
        request = await create_saved_request(
            session, "Only Pick me", "SELECT id FROM ai_cards WHERE title = 'Pick me'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        request_id = request.id

    services = services_for(sessions)
    screen = FakeMessage(103, bot_message=True)
    await render_plan(screen, services, filters=[request_id])
    assert "Skip me (2)" not in button_texts(screen.edits[-1][1])

    await handle_plan_start(screen, services, f"sp-{ids['pick']}")
    card_text, card_markup = screen.bot.edits[-1][1], screen.bot.edits[-1][2]
    assert "Pick me" in card_text

    back = next(
        button
        for row in card_markup.inline_keyboard
        for button in row
        if button.text.endswith("Back")
    )
    await callback_token_handler(
        FakeCallback(back.callback_data.split(":", 1)[1], screen), services
    )

    labels = button_texts(screen.edits[-1][1])
    assert "Pick me (1)" in labels
    assert "Skip me (2)" not in labels
    assert "Apply filter (1)" in " ".join(labels)


async def test_pl_plan_017_a_filter_that_matches_nothing_says_so_instead_of_going_blank(sessions):
    """PL-PLAN-017 — tests/brd/planning.feature"""
    await seed_plan(sessions)
    async with sessions() as session:
        request = await create_saved_request(
            session, "Nothing", "SELECT id FROM ai_cards WHERE title = 'No such Card'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        request_id = request.id

    services = services_for(sessions)
    screen = FakeMessage(104, bot_message=True)
    await render_plan(screen, services, filters=[request_id])

    labels = button_texts(screen.edits[-1][1])
    assert "0 of 2 Actions match" in labels
    assert "Into Sprint" not in " ".join(labels)


async def test_pl_plan_017_the_filter_screen_toggles_a_request_on_and_off(sessions) -> None:
    """PL-PLAN-017 — tests/brd/planning.feature"""
    await seed_plan(sessions)
    async with sessions() as session:
        request = await create_saved_request(
            session, "Only Pick me", "SELECT id FROM ai_cards WHERE title = 'Pick me'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        request_id = request.id

    services = services_for(sessions)
    screen = FakeMessage(105, bot_message=True)
    await render_plan(screen, services)

    opener = next(
        button
        for row in screen.edits[-1][1].inline_keyboard
        for button in row
        if "Apply filter" in button.text
    )
    await callback_token_handler(
        FakeCallback(opener.callback_data.split(":", 1)[1], screen), services
    )
    unchecked = next(
        button
        for row in screen.edits[-1][1].inline_keyboard
        for button in row
        if button.text.endswith("Only Pick me")
    )
    assert unchecked.text.startswith("☐")

    await callback_token_handler(
        FakeCallback(unchecked.callback_data.split(":", 1)[1], screen), services
    )
    checked = next(
        button
        for row in screen.edits[-1][1].inline_keyboard
        for button in row
        if button.text.endswith("Only Pick me")
    )
    assert checked.text.startswith("☑")
    assert await plan_filters(sessions) == [request_id]

    back = next(
        button
        for row in screen.edits[-1][1].inline_keyboard
        for button in row
        if button.text.endswith("Back")
    )
    await callback_token_handler(
        FakeCallback(back.callback_data.split(":", 1)[1], screen), services
    )
    labels = button_texts(screen.edits[-1][1])
    assert labels.count("\U0001f4e5 Into Sprint") == 1
    assert "Pick me (1)" in labels


async def test_pl_plan_019_a_burst_of_link_taps_earns_a_warning(sessions, monkeypatch) -> None:
    """PL-PLAN-019 — tests/brd/planning.feature"""
    import safwa.shell.chat as messaging

    monkeypatch.setattr(messaging, "TOAST_SECONDS", 0)
    ids = await seed_plan(sessions)
    services = services_for(sessions)
    screen = FakeMessage(106, bot_message=True, answer_as_new=True)
    await render_plan(screen, services)

    payload = f"sp-{ids['pick']}"
    for _ in range(PLAN_LINK_BURST_TAPS - 1):
        await handle_plan_start(screen, services, payload)
    assert screen.answers == []

    await handle_plan_start(screen, services, payload)
    assert "link taps" in screen.answers[-1]
    assert "hours" in screen.answers[-1]
    # The tap that earned the warning still opened the Card it points at.
    assert "Pick me" in screen.bot.edits[-1][1]

    _, expiry = services.chat.toasts[screen.chat.id]
    await expiry


async def test_pl_plan_018_the_plans_cost_is_shown_against_the_capacity(sessions) -> None:
    """PL-PLAN-018 — tests/brd/planning.feature"""
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        await create_card(
            session, kind="action", title="Heavy", stage="sprint", effort_points=13
        )
        await set_sprint_success_criteria(session, "Ship v2")
        await session.commit()

    services = services_for(sessions)
    planning = FakeMessage(120, bot_message=True)
    await render_sprint(planning, services)

    assert "Planned: 1 Actions · 13 EP · capacity — EP" in planning.edits[-1][0]
    assert "Above configured capacity" not in planning.edits[-1][0]

    async with sessions() as session:
        await set_profile_field(
            session, ProfileField.CAPACITY_EFFORT_POINTS, 10, clock=SystemClock()
        )
        await session.commit()

    await render_sprint(planning, services)
    assert "Planned: 1 Actions · 13 EP · capacity 10 EP" in planning.edits[-1][0]
    assert "⚠️ Above configured capacity (10 EP)." in planning.edits[-1][0]
    # Above the capacity is advice: the Sprint still starts.
    assert any(label.startswith("▶️ Start") for label in button_texts(planning.edits[-1][1]))

    plan = FakeMessage(121, bot_message=True)
    await render_plan(plan, services)

    assert "In Sprint: 1 Actions · 13 EP · capacity 10 EP" in plan.edits[-1][0]
    assert "Above configured capacity" in plan.edits[-1][0]
