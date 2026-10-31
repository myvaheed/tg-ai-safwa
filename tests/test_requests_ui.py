"""The Request screens: the list, one Request run, and what deleting one clears."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from ui_harness import (
    CALLBACK_ACTIONS,
    FakeCallback,
    FakeMessage,
    button_texts,
    seed_plan,
    services_for,
)

from safwa.bootstrap.main import bootstrap_workspace
from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS
from safwa.features.cards.telegram import RESULT_LIMIT
from safwa.features.cards.telegram.presentation import ITEM_BUTTON_LIMIT
from safwa.features.cards.use_cases import (
    archive_subtree,
    create_card,
    finish_action,
    finish_card,
)
from safwa.features.checks.use_cases import create_check, delete_check
from safwa.features.planning.telegram import render_plan
from safwa.features.saved_requests.api import request_items
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.telegram import command_requests
from safwa.features.saved_requests.use_cases import (
    create_saved_request,
    delete_saved_request,
    seed_default_requests,
)
from safwa.features.values.use_cases import create_value
from tg_agent_shell.ai.sql import create_ai_views
from tg_agent_shell.telegram import callback_token_handler, open_item_screen


async def _one_request_over_actions(sessions, *, cards: int, name: str = "Open actions"):
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        for index in range(cards):
            await create_card(
                session, kind="action", title=f"Action {index:02d}", effort_points=1
            )
        request = await create_saved_request(
            session,
            name,
            "SELECT 'card' AS item_type, id FROM ai_cards WHERE kind = 'action' ORDER BY title",
            "Everything still open.",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        return request.id

async def test_a_request_is_never_written_by_hand(sessions) -> None:
    """SR-WRITE-001 — tests/brd/saved_requests.feature"""
    request_id = await _one_request_over_actions(sessions, cards=1)
    services = services_for(sessions)
    screen = FakeMessage(940, bot_message=True)
    await open_item_screen(screen, services, "request", request_id)

    labels = button_texts(screen.edits[-1][1])
    # The screen runs the Request and navigates. Nothing on it authors one.
    assert not any(
        word in label.casefold()
        for label in labels
        for word in ("new", "create", "edit", "rename", "sql")
    )
    # The other two navigate: one explains what a Request is, the other is its way back.
    assert sorted(name for name in CALLBACK_ACTIONS if "request" in name) == [
        "request_about",
        "request_list",
        "request_view",
    ]


async def test_the_requests_screen_lists_runs_and_comes_back(sessions) -> None:
    """SR-UI-012 — tests/brd/saved_requests.feature"""
    request_id = await _one_request_over_actions(sessions, cards=RESULT_LIMIT + 5)
    services = services_for(sessions)

    listing = FakeMessage(941, bot_message=True)
    await command_requests(listing, services)
    assert f"Open actions · ⭐️{RESULT_LIMIT + 5}" in button_texts(listing.edits[-1][1])

    detail = FakeMessage(942, bot_message=True)
    await open_item_screen(detail, services, "request", request_id)
    body, markup = detail.edits[-1]
    assert "Everything still open." in body
    assert f"⭐️{RESULT_LIMIT + 5}" in body
    assert f"showing first {RESULT_LIMIT}" in body
    labels = button_texts(markup)
    assert sum(label.startswith("⭐️") for label in labels) == RESULT_LIMIT
    assert "↻ Refresh" in labels

    card_button = next(
        item for row in markup.inline_keyboard for item in row if item.text.startswith("⭐️")
    )
    await callback_token_handler(
        FakeCallback(card_button.callback_data.split(":", 1)[1], detail), services
    )
    card_markup = detail.edits[-1][1]
    back = next(
        item
        for row in card_markup.inline_keyboard
        for item in row
        if item.text.endswith("Back")
    )
    await callback_token_handler(
        FakeCallback(back.callback_data.split(":", 1)[1], detail), services
    )
    assert "Open actions" in detail.edits[-1][0]


async def test_deleting_a_request_takes_it_off_every_surface(sessions) -> None:
    """SR-DELETE-015 — tests/brd/saved_requests.feature"""
    await seed_plan(sessions)
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        request = await create_saved_request(
            session, "Only Pick me", "SELECT 'card' AS item_type, id FROM ai_cards WHERE title = 'Pick me'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        request_id = request.id

    services = services_for(sessions)
    listing = FakeMessage(943, bot_message=True)
    await command_requests(listing, services)
    assert "Only Pick me · ⭐️1" in button_texts(listing.edits[-1][1])

    async with sessions() as session:
        await delete_saved_request(session, request_id)
        await session.commit()

    async with sessions() as session:
        assert (await session.execute(text("SELECT id FROM ai_requests"))).all() == []
        assert (await session.get(SavedRequest, request_id)) is None

    after = FakeMessage(944, bot_message=True)
    await command_requests(after, services)
    assert not any(label.startswith("Only Pick me · ") for label in button_texts(after.edits[-1][1]))

    # A filter picking it is simply not picking anything: the whole Backlog comes back.
    screen = FakeMessage(945, bot_message=True)
    await render_plan(screen, services, filters=[request_id])
    labels = button_texts(screen.edits[-1][1])
    assert "Pick me" in labels and "Skip me" in labels

async def test_sr_ui_013_a_new_workspace_starts_with_requests_and_a_screen_about_them(
    sessions,
) -> None:
    """SR-UI-013 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        await create_card(session, kind="action", title="Run", effort_points=1)
        seeded = await seed_default_requests(session, views=ALLOWED_VIEWS)
        await session.commit()
        assert [request.name for request in seeded] == [
            "Goals without children", "Goals without Values", "Values without Goals"
        ]
        goal_id, goals_id = goal.id, seeded[0].id

    async with sessions() as session:
        # An ordinary Request: it runs down the same path every other Request runs.
        request = await session.get(SavedRequest, goals_id)
        found = await request_items(session, request.query_sql, ALLOWED_VIEWS)
        assert [card.id for card in found] == [goal_id]
        await delete_saved_request(session, goals_id)
        await session.commit()
        assert await session.get(SavedRequest, goals_id) is None
        # The defaults are written when the workspace is created, and this is what says so:
        # a later start reports no creation, so a deleted Request is never written again.
        assert (await bootstrap_workspace(session, 42, "Europe/Istanbul"))[1] is False

    services = services_for(sessions)
    message = FakeMessage(950, bot_message=True)
    await command_requests(message, services)
    labels = button_texts(message.edits[-1][1])
    assert not any(label.startswith("Goals without children · ") for label in labels)
    about = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == "❓ О Запросах"
    )
    await callback_token_handler(
        FakeCallback(about.callback_data.split(":", 1)[1], message), services
    )
    text, markup = message.edits[-1]
    assert "<b>О Запросах</b>" in text
    assert "Теги" in text
    assert "Safwa" in text

    back = next(
        button for row in markup.inline_keyboard for button in row if button.text == "↩️ Back"
    )
    await callback_token_handler(
        FakeCallback(back.callback_data.split(":", 1)[1], message), services
    )
    assert "❓ О Запросах" in button_texts(message.edits[-1][1])


async def test_a_request_opened_from_the_list_goes_back_to_it(sessions) -> None:
    """SC-BACK-012 — tests/brd/tg_agent_shell/screens.feature"""
    await seed_plan(sessions)
    async with sessions() as session:
        await create_saved_request(
            session, "Only Pick me", "SELECT 'card' AS item_type, id FROM ai_cards WHERE title = 'Pick me'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
    services = services_for(sessions)
    listing = FakeMessage(950, bot_message=True)
    await command_requests(listing, services)

    opened = listing.edits[-1][1].inline_keyboard[0][0]
    await callback_token_handler(FakeCallback(opened.callback_data.split(":", 1)[1], listing), services)
    assert "<b>Only Pick me</b>" in listing.edits[-1][0]
    labels = button_texts(listing.edits[-1][1])
    assert labels[-1] == "↩️ Back" and "↩️ Menu" not in labels

    back = listing.edits[-1][1].inline_keyboard[-1][0]
    await callback_token_handler(FakeCallback(back.callback_data.split(":", 1)[1], listing), services)
    assert "<b>Requests</b>" in listing.edits[-1][0]


async def test_cd_order_054_a_result_reads_as_one_flat_tree(sessions) -> None:
    """CD-ORDER-054 — tests/brd/cards.feature"""
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )

        async def card(title: str, kind: str = "action", parent=None, also=None):
            return await create_card(
                session,
                kind=kind,
                title=title,
                effort_points=1 if kind == "action" else None,
                parent_ids={held.id for held in (parent, also) if held is not None},
            )

        await card("Loose")
        small = await card("Small", "goal")
        await card("Small step", parent=small)
        big = await card("Big", "goal")
        middle = await card("Middle", "goal", big)
        await card("Middle step", parent=middle)
        # Under two Goals, it comes under the last of them the list reaches.
        await card("Shared step", parent=small, also=middle)
        big_done = await card("Big done", parent=big)
        finished = await card("Finished", "goal")
        below_finished = await card("Below finished", "goal", finished)
        finished_step = await card("Finished step", parent=below_finished)
        old = await card("Old", "goal")
        old_step = await card("Old step", parent=old)
        for action in (big_done, finished_step, old_step):
            await finish_action(session, action.id)
        await finish_card(session, finished.id)
        await finish_card(session, old.id)
        await archive_subtree(session, old.id)
        request = await create_saved_request(
            session, "Everything", "SELECT 'card' AS item_type, id FROM ai_cards", views=ALLOWED_VIEWS
        )
        await session.commit()
        request_id = request.id

    screen = FakeMessage(960, bot_message=True)
    await open_item_screen(screen, services_for(sessions), "request", request_id)
    labels = [
        label
        for label in button_texts(screen.edits[-1][1])
        if label not in {"↻ Refresh", "↩️ Menu"}
    ]
    assert [label.split(" ", 1)[1].rsplit(" · ", 1)[0] for label in labels] == [
        # Live: the Goal with more levels first, each with its children right under it.
        "Big",
        "Middle",
        "Middle step",
        "Small",
        "Small step",
        "Shared step",
        "Loose",
        # Done: a Done Goal keeps its whole branch, and a Done Action leaves its open Goal.
        "Finished",
        "Below finished",
        "Finished step",
        "Big done",
        # Archived last.
        "Old",
        "Old step",
    ]
    assert labels[0].startswith("🎯² ")


async def test_explicit_request_order_takes_precedence_over_result_hierarchy(sessions):
    """CD-ORDER-054 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Goal", priority="critical")
        action = await create_card(session, kind="action", title="Action", parent_ids={goal.id})
        await finish_action(session, action.id)
        request = await create_saved_request(
            session, "Latest", "SELECT 'card' AS item_type, id FROM ai_cards ORDER BY id DESC", views=ALLOWED_VIEWS
        )
        await session.commit()

    screen = FakeMessage(961, bot_message=True)
    await open_item_screen(screen, services_for(sessions), "request", request.id)
    labels = button_texts(screen.edits[-1][1])
    assert "Action" in labels[0]
    assert "Goal" in labels[1]


@pytest.mark.parametrize("query, expected", [
    ("SELECT 'card' AS item_type, id FROM ai_cards", ["Critical", "Medium", "Low"]),
    ("SELECT 'card' AS item_type, id FROM ai_cards LIMIT 2", ["Medium", "Low"]),
    ("SELECT 'card' AS item_type, id FROM ai_cards LIMIT 1 OFFSET 1", ["Medium"]),
    ("SELECT 'card' AS item_type, id, 'ORDER BY id' AS note FROM ai_cards", ["Critical", "Medium", "Low"]),
    ("SELECT 'card' AS item_type, id FROM ai_cards ORDER BY id", ["Low", "Medium", "Critical"]),
    ("SELECT 'card' AS item_type, id FROM ai_cards WHERE id IN (SELECT id FROM ai_cards ORDER BY id LIMIT 3)",
     ["Critical", "Medium", "Low"]),
    ("WITH chosen AS (SELECT 'card' AS item_type, id FROM ai_cards ORDER BY id) SELECT item_type, id FROM chosen",
     ["Critical", "Medium", "Low"]),
    ("SELECT 'card' AS item_type, id, row_number() OVER (ORDER BY id) AS n FROM ai_cards",
     ["Critical", "Medium", "Low"]),
])
async def test_request_screen_sorts_only_the_selected_cards(sessions, query, expected):
    """CD-ORDER-064 — tests/brd/cards.feature"""
    async with sessions() as session:
        for title in ("Low", "Medium", "Critical"):
            await create_card(session, kind="action", title=title, priority=title.lower())
        request = await create_saved_request(session, "Selection", query, views=ALLOWED_VIEWS)
        await session.commit()

    screen = FakeMessage(962, bot_message=True)
    await open_item_screen(screen, services_for(sessions), "request", request.id)
    labels = button_texts(screen.edits[-1][1])
    assert [label.split(" ", 1)[1].rsplit(" · ", 1)[0] for label in labels[:-2]] == expected


async def test_request_screen_sorts_before_its_display_cap(sessions):
    """CD-ORDER-064 — tests/brd/cards.feature"""
    async with sessions() as session:
        for i in range(RESULT_LIMIT):
            await create_card(session, kind="action", title=f"Ordinary {i}")
        await create_card(session, kind="action", title="Critical", priority="critical")
        request = await create_saved_request(
            session, "Selection", "SELECT 'card' AS item_type, id FROM ai_cards", views=ALLOWED_VIEWS
        )
        await session.commit()

    screen = FakeMessage(963, bot_message=True)
    await open_item_screen(screen, services_for(sessions), "request", request.id)
    labels = button_texts(screen.edits[-1][1])
    assert "Critical" in labels[0]
    assert len(labels[:-2]) == RESULT_LIMIT
    assert not any(f"Ordinary {RESULT_LIMIT - 1}" in label for label in labels)


async def test_request_counts_cover_the_full_selection_and_survive_a_bad_request(sessions):
    """SR-UI-012 — tests/brd/saved_requests.feature"""
    name = "Selection " * 20
    async with sessions() as session:
        await create_card(session, kind="goal", title="Goal")
        await create_card(session, kind="action", title="Action")
        checks = [
            await create_check(session, title=f"Check {index:02d}?")
            for index in range(RESULT_LIMIT + 1)
        ]
        mixed = await create_saved_request(
            session, name,
            "SELECT 'card' AS item_type, id FROM ai_cards "
            "UNION ALL SELECT 'check', id FROM ai_checks "
            "UNION ALL SELECT 'check', id FROM ai_checks",
            views=ALLOWED_VIEWS,
        )
        await create_saved_request(
            session, "Empty",
            "SELECT 'check' AS item_type, id FROM ai_checks WHERE id < 0",
            views=ALLOWED_VIEWS,
        )
        session.add(SavedRequest(name="Broken", query_sql="SELECT id FROM cards"))
        await session.commit()

    services = services_for(sessions)
    listing = FakeMessage(970, bot_message=True)
    await command_requests(listing, services)
    labels = button_texts(listing.edits[-1][1])
    assert "Broken · ⚠️" in labels
    assert "Empty · 0" in labels
    label = next(label for label in labels if label.startswith("Selection"))
    assert label.endswith(f" · 🎯1 · ⭐️1 · ☑️{len(checks)}")
    assert len(label) == ITEM_BUTTON_LIMIT

    detail = FakeMessage(971, bot_message=True)
    await open_item_screen(detail, services, "request", mixed.id)
    body, markup = detail.edits[-1]
    assert f"🎯1 · ⭐️1 · ☑️{len(checks)} (showing first {RESULT_LIMIT})" in body
    assert len(markup.inline_keyboard) == RESULT_LIMIT + 2

    async with sessions() as session:
        await delete_check(session, checks[-1].id)
        await session.commit()
    refresh = next(
        button for row in markup.inline_keyboard for button in row if button.text == "↻ Refresh"
    )
    await callback_token_handler(FakeCallback(refresh.callback_data.split(":", 1)[1], detail), services)
    assert f"☑️{len(checks) - 1}" in detail.edits[-1][0]
    await command_requests(listing, services)
    assert any(
        label.endswith(f" · 🎯1 · ⭐️1 · ☑️{len(checks) - 1}")
        for label in button_texts(listing.edits[-1][1])
    )


@pytest.mark.parametrize("ordered", [False, True])
async def test_mixed_request_order_and_check_value_navigation(sessions, ordered):
    """SR-UI-012 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Walk")
        check = await create_check(session, title="Enough sleep?")
        await create_value(session, "Health <&>", "Stay healthy.")
        check.archived_at = check.created_at
        await create_saved_request(
            session, "Mixed",
            "SELECT 'value' AS item_type, id, 0 AS position FROM ai_values "
            "UNION ALL SELECT 'check', id, 1 FROM ai_checks "
            "UNION ALL SELECT 'card', id, 2 FROM ai_cards"
            + (" ORDER BY position" if ordered else ""),
            views=ALLOWED_VIEWS,
        )
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(972, bot_message=True)
    await command_requests(message, services)
    entry = next(
        button for row in message.edits[-1][1].inline_keyboard for button in row
        if button.text == "Mixed · ⭐️1 · ☑️1 · 💎1"
    )
    await callback_token_handler(FakeCallback(entry.callback_data.split(":", 1)[1], message), services)
    markup = message.edits[-1][1]
    labels = button_texts(markup)
    assert [label[:1] for label in labels[:3]] == (["💎", "☑", "⭐"] if ordered else ["⭐", "💎", "☑"])
    value_button = next(
        button for row in markup.inline_keyboard for button in row if button.text.startswith("💎")
    )
    await callback_token_handler(
        FakeCallback(value_button.callback_data.split(":", 1)[1], message), services
    )
    assert "Health &lt;&amp;&gt;" in message.edits[-1][0]
    back = message.edits[-1][1].inline_keyboard[-1][0]
    await callback_token_handler(FakeCallback(back.callback_data.split(":", 1)[1], message), services)
    assert "<b>Mixed</b>" in message.edits[-1][0]
    markup = message.edits[-1][1]
    check_button = next(
        button for row in markup.inline_keyboard for button in row if button.text.startswith("☑️")
    )
    assert "[📦]" in check_button.text
    await callback_token_handler(
        FakeCallback(check_button.callback_data.split(":", 1)[1], message), services
    )
    assert "Enough sleep?" in message.edits[-1][0]
    for expected in ("<b>Mixed</b>", "<b>Requests</b>"):
        back = next(
            button for row in message.edits[-1][1].inline_keyboard for button in row
            if button.text == "↩️ Back"
        )
        await callback_token_handler(FakeCallback(back.callback_data.split(":", 1)[1], message), services)
        assert expected in message.edits[-1][0]
