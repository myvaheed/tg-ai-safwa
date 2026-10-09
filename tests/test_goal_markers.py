from __future__ import annotations

import pytest
from schedule_helpers import create_card
from ui_harness import FakeMessage, button_texts, services_for

from safwa.features.cards.telegram import render_backlog, render_card, render_children
from safwa.features.cards.telegram.presentation import (
    ITEM_BUTTON_LIMIT,
    card_citation_label,
    card_emoji,
    item_button_label,
)
from safwa.features.cards.use_cases import delete_one_card, set_card_parent
from safwa.features.saved_requests.telegram.screens import render_saved_request
from safwa.features.saved_requests.use_cases import create_saved_request


@pytest.mark.parametrize(
    "nesting, marker",
    [
        (0, "🎯"),
        (1, "↳🎯"),
        (2, "↳↳🎯"),
        (3, "↳(3)🎯"),
        (4, "↳(4)🎯"),
        (5, "↳(5)🎯"),
        (6, "↳(6)🎯"),
    ],
)
async def test_cd_button_048_goal_markers_count_parents_on_every_surface(sessions, nesting, marker):
    """CD-BUTTON-048 — tests/brd/cards.feature"""
    services = services_for(sessions)
    async with sessions() as session:
        parent = None
        for level in range(nesting):
            parent = await create_card(
                session,
                kind="goal",
                title=f"Ancestor {level}",
                parent_id=parent.id if parent else None,
            )
        goal = await create_card(
            session,
            kind="goal",
            title="Target " + "X" * 100,
            parent_id=parent.id if parent else None,
        )
        label = await item_button_label(session, goal)
        assert label.startswith(marker + " ") and len(label) == ITEM_BUTTON_LIMIT
        citation = await card_citation_label(session, services, goal)
        assert citation.startswith(marker + " ")
        request = await create_saved_request(
            session,
            "Marker goals",
            "SELECT id FROM ai_cards WHERE kind = 'goal'",
            views=services.views,
        )
        await session.commit()
        goal_id, parent_id, request_id = goal.id, goal.parent_id, request.id

    message = FakeMessage(80, bot_message=True)
    await render_card(message, services, goal_id)
    name = "Subgoal" if nesting else "Goal"
    assert f"Kind: {marker} {name}" in message.edits[-1][0]
    await render_backlog(message, services, kinds="goals")
    assert marker + " Target " in message.edits[-1][0]
    await render_saved_request(message, services, request_id)
    assert label in button_texts(message.edits[-1][1])
    if parent_id is not None:
        await render_children(message, services, parent_id)
        assert label in button_texts(message.edits[-1][1])


async def test_cd_button_048_markers_follow_changes_to_ancestors(sessions):
    """CD-BUTTON-048 — tests/brd/cards.feature"""
    async with sessions() as session:
        root = await create_card(session, kind="goal", title="Root")
        parent = await create_card(session, kind="goal", title="Parent", parent_id=root.id)
        goal = await create_card(session, kind="goal", title="Child", parent_id=parent.id)
        assert await card_emoji(session, goal) == "↳↳🎯"
        await set_card_parent(session, parent.id, None)
        assert await card_emoji(session, goal) == "↳🎯"
        await delete_one_card(session, parent.id)
        assert await card_emoji(session, goal) == "🎯"
