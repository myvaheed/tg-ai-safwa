"""The Request screens: the list, one Request run, and what deleting one clears."""

from __future__ import annotations

from sqlalchemy import text
from ui_harness import (
    CALLBACK_ACTIONS,
    FakeCallback,
    FakeMessage,
    button_texts,
    seed_plan,
    services_for,
)

from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS
from safwa.constants import REQUEST_RESULT_LIMIT
from safwa.features.cards.use_cases import create_card
from safwa.features.planning.telegram import render_plan
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.telegram import command_requests
from safwa.features.saved_requests.use_cases import create_saved_request, delete_saved_request
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
            "SELECT id FROM ai_cards WHERE kind = 'action' ORDER BY title",
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
    assert [name for name in CALLBACK_ACTIONS if "request" in name] == ["request_view"]


async def test_the_requests_screen_lists_runs_and_comes_back(sessions) -> None:
    """SR-UI-012 — tests/brd/saved_requests.feature"""
    request_id = await _one_request_over_actions(sessions, cards=REQUEST_RESULT_LIMIT + 5)
    services = services_for(sessions)

    listing = FakeMessage(941, bot_message=True)
    await command_requests(listing, services)
    assert "Open actions" in button_texts(listing.edits[-1][1])

    detail = FakeMessage(942, bot_message=True)
    await open_item_screen(detail, services, "request", request_id)
    body, markup = detail.edits[-1]
    assert "Everything still open." in body
    assert f"{REQUEST_RESULT_LIMIT + 5} matching cards" in body
    assert f"showing first {REQUEST_RESULT_LIMIT}" in body
    labels = button_texts(markup)
    assert sum(label.startswith("⭐️") for label in labels) == REQUEST_RESULT_LIMIT
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
    """SR-DELETE-013 — tests/brd/saved_requests.feature"""
    await seed_plan(sessions)
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        request = await create_saved_request(
            session, "Only Pick me", "SELECT id FROM ai_cards WHERE title = 'Pick me'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        request_id = request.id

    services = services_for(sessions)
    listing = FakeMessage(943, bot_message=True)
    await command_requests(listing, services)
    assert "Only Pick me" in button_texts(listing.edits[-1][1])

    async with sessions() as session:
        await delete_saved_request(session, request_id)
        await session.commit()

    async with sessions() as session:
        assert (await session.execute(text("SELECT id FROM ai_requests"))).all() == []
        assert (await session.get(SavedRequest, request_id)) is None

    after = FakeMessage(944, bot_message=True)
    await command_requests(after, services)
    assert "Only Pick me" not in button_texts(after.edits[-1][1])

    # A filter picking it is simply not picking anything: the whole Backlog comes back.
    screen = FakeMessage(945, bot_message=True)
    await render_plan(screen, services, filters=[request_id])
    labels = button_texts(screen.edits[-1][1])
    assert "Pick me (1)" in labels and "Skip me (2)" in labels
