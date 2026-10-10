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
    "depth, marker, child_marker",
    [
        (0, "🎯", None),
        (1, "🎯", "🎯"),
        (2, "🎯²", "🎯"),
        (3, "🎯³", "🎯²"),
        (4, "🎯⁴", "🎯³"),
        (5, "🎯⁵", "🎯⁴"),
        (6, "🎯⁶", "🎯⁵"),
    ],
)
async def test_cd_button_048_goal_markers_show_subgoal_depth_on_every_surface(sessions, depth, marker, child_marker):
    """CD-BUTTON-048 — tests/brd/cards.feature"""
    services = services_for(sessions)
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Target " + "X" * 100)
        leaf = goal
        for level in range(depth):
            leaf = await create_card(
                session,
                kind="goal",
                title=f"Child {level}",
                parent_id=leaf.id,
            )
        if depth:
            await create_card(session, kind="goal", title="Sibling", parent_id=goal.id)
        await create_card(session, kind="action", title="Work", parent_id=goal.id)
        assert await card_emoji(session, leaf) == "🎯"
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
        goal_id, leaf_id, request_id = goal.id, leaf.id, request.id

    message = FakeMessage(80, bot_message=True)
    await render_card(message, services, goal_id)
    assert f"Kind: {marker} Goal" in message.edits[-1][0]
    await render_backlog(message, services, kinds="goals")
    assert marker + " Target " in message.edits[-1][0]
    await render_saved_request(message, services, request_id)
    assert label in button_texts(message.edits[-1][1])
    await render_children(message, services, goal_id)
    assert "⭐️ Work · 📚" in button_texts(message.edits[-1][1])
    if depth:
        assert f"{child_marker} Child 0 · 📚" in button_texts(message.edits[-1][1])
        assert "🎯 Sibling · 📚" in button_texts(message.edits[-1][1])
        await render_card(message, services, leaf_id)
        assert "Kind: 🎯 Subgoal" in message.edits[-1][0]


async def test_cd_button_048_markers_follow_moves_and_single_card_deletion(sessions):
    """CD-BUTTON-048 — tests/brd/cards.feature"""
    async with sessions() as session:
        root = await create_card(session, kind="goal", title="Root")
        parent = await create_card(session, kind="goal", title="Parent", parent_id=root.id)
        goal = await create_card(session, kind="goal", title="Child", parent_id=parent.id)
        assert await card_emoji(session, root) == "🎯²"
        assert await card_emoji(session, parent) == "🎯"
        assert await card_emoji(session, goal) == "🎯"
        await set_card_parent(session, parent.id, None)
        assert await card_emoji(session, root) == "🎯"
        assert await card_emoji(session, parent) == "🎯"
        assert await card_emoji(session, goal) == "🎯"
        await set_card_parent(session, parent.id, root.id)
        assert await card_emoji(session, root) == "🎯²"
        await delete_one_card(session, parent.id)
        assert await card_emoji(session, root) == "🎯"
        assert await card_emoji(session, goal) == "🎯"
