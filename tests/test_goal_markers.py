from __future__ import annotations

import pytest
from schedule_helpers import create_card
from ui_harness import FakeMessage, button_texts, services_for

from safwa.features.cards.api import tree_level
from safwa.features.cards.model import CARD_TREE_DEPTH_MAX
from safwa.features.cards.telegram import render_backlog, render_card, render_children
from safwa.features.cards.telegram.presentation import (
    ITEM_BUTTON_LIMIT,
    card_citation_label,
    card_emoji,
    item_button_label,
)
from safwa.features.cards.use_cases import delete_card, toggle_card_parent
from safwa.features.saved_requests.telegram.screens import render_saved_request
from safwa.features.saved_requests.use_cases import create_saved_request


@pytest.mark.parametrize(
    "depth, marker, child_marker",
    [
        (0, "🎯", None),
        (1, "🎯²", "🎯"),
        (2, "🎯³", "🎯²"),
        (3, "🎯⁴", "🎯³"),
        (4, "🎯⁵", "🎯⁴"),
        (5, "🎯⁶", "🎯⁵"),
        (6, "🎯⁷", "🎯⁶"),
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
                parent_ids={leaf.id},
            )
        if depth:
            await create_card(session, kind="goal", title="Sibling", parent_ids={goal.id})
        await create_card(session, kind="action", title="Work", parent_ids={goal.id})
        assert await card_emoji(session, leaf) == "🎯"
        label = await item_button_label(session, goal)
        assert label.startswith(marker + " ") and len(label) == ITEM_BUTTON_LIMIT
        citation = await card_citation_label(session, services, goal, short=True)
        assert citation.startswith(marker + " ")
        request = await create_saved_request(
            session,
            "Marker goals",
            "SELECT 'card' AS item_type, id FROM ai_cards WHERE kind = 'goal'",
            views=services.views,
        )
        await session.commit()
        goal_id, leaf_id, request_id = goal.id, leaf.id, request.id

    message = FakeMessage(80, bot_message=True)
    await render_card(message, services, goal_id)
    assert f"<i>Kind:</i> {marker} Goal" in message.edits[-1][0]
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
        assert "<i>Kind:</i> 🎯 Subgoal" in message.edits[-1][0]


async def test_cd_button_048_markers_follow_moves_and_single_card_deletion(sessions):
    """CD-BUTTON-048 — tests/brd/cards.feature"""
    async with sessions() as session:
        root = await create_card(session, kind="goal", title="Root")
        parent = await create_card(session, kind="goal", title="Parent", parent_ids={root.id})
        goal = await create_card(session, kind="goal", title="Child", parent_ids={parent.id})
        assert await card_emoji(session, root) == "🎯³"
        assert await card_emoji(session, parent) == "🎯²"
        assert await card_emoji(session, goal) == "🎯"
        await toggle_card_parent(session, parent.id, root.id)
        assert await card_emoji(session, root) == "🎯"
        assert await card_emoji(session, parent) == "🎯²"
        assert await card_emoji(session, goal) == "🎯"
        await toggle_card_parent(session, parent.id, root.id)
        assert await card_emoji(session, root) == "🎯³"
        # A second parent counts its own deepest chain.
        other = await create_card(session, kind="goal", title="Other")
        await toggle_card_parent(session, goal.id, other.id)
        assert await card_emoji(session, other) == "🎯²"
        assert await card_emoji(session, root) == "🎯³"
        await delete_card(session, parent.id)
        assert await card_emoji(session, root) == "🎯"
        assert await card_emoji(session, goal) == "🎯"


async def test_cd_button_048_goal_subgoal_action_chain_counts_only_goal_levels(sessions):
    """CD-BUTTON-048 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Goal")
        subgoal = await create_card(session, kind="goal", title="Subgoal", parent_ids={goal.id})
        action = await create_card(session, kind="action", title="Action", parent_ids={subgoal.id})
        assert await card_emoji(session, goal) == "🎯²"
        assert await card_emoji(session, subgoal) == "🎯"
        assert await card_emoji(session, action) == "⭐️"


async def test_cd_tree_053_ai_cards_reads_the_goal_marker_as_tree_level(read_views):
    """CD-TREE-053 — tests/brd/cards.feature"""
    sessions, runner = read_views

    async def read_levels():
        rows = (await runner.run("SELECT id, tree_level FROM ai_cards")).rows
        return {row["id"]: row["tree_level"] for row in rows}

    async with sessions() as session:
        chain = [await create_card(session, kind="goal", title="Goal")]
        for level in range(3):
            chain.append(
                await create_card(
                    session, kind="goal", title=f"Subgoal {level}", parent_ids={chain[-1].id}
                )
            )
        sibling = await create_card(session, kind="goal", title="Sibling", parent_ids={chain[0].id})
        action = await create_card(session, kind="action", title="Action", parent_ids={chain[-1].id})
        await session.commit()
        assert await card_emoji(session, chain[0]) == "🎯⁴"
        ids = [card.id for card in chain]
        sibling_id, action_id = sibling.id, action.id

    levels = await read_levels()
    assert [levels[card_id] for card_id in ids] == [4, 3, 2, 1]
    assert (levels[sibling_id], levels[action_id]) == (1, 0)

    async with sessions() as session:
        await toggle_card_parent(session, ids[2], ids[1])
        await session.commit()
    levels = await read_levels()
    assert [levels[card_id] for card_id in ids] == [2, 1, 2, 1]

    async with sessions() as session:
        await delete_card(session, ids[3])
        await session.commit()
    assert (await read_levels())[ids[2]] == 1


@pytest.mark.parametrize("link", [False, True])
async def test_cd_tree_053_seven_goals_with_an_action_keep_the_root_marker_seven(read_views, link):
    """CD-TREE-053 — tests/brd/cards.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        chain = [await create_card(session, kind="goal", title="Goal")]
        for level in range(6):
            chain.append(
                await create_card(
                    session, kind="goal", title=f"Subgoal {level}", parent_ids={chain[-1].id}
                )
            )
        action = await create_card(
            session, kind="action", title="Action", parent_ids=set() if link else {chain[-1].id}
        )
        if link:
            await toggle_card_parent(session, action.id, chain[-1].id)
        assert await card_emoji(session, chain[0]) == "🎯⁷"
        await session.commit()
        root_id, action_id = chain[0].id, action.id

    rows = (await runner.run("SELECT id, tree_level FROM ai_cards")).rows
    levels = {row["id"]: row["tree_level"] for row in rows}
    assert levels[root_id] == 7
    assert levels[action_id] == 0


async def test_cd_tree_053_a_chain_at_the_depth_limit_reads_the_same_as_its_markers(read_views):
    """CD-TREE-053 — tests/brd/cards.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        chain = [await create_card(session, kind="goal", title="Goal")]
        for level in range(CARD_TREE_DEPTH_MAX - 1):
            chain.append(
                await create_card(
                    session, kind="goal", title=f"Subgoal {level}", parent_ids={chain[-1].id}
                )
            )
        await session.commit()
        counted = {card.id: await tree_level(session, card.kind, card.id) for card in chain}

    rows = (await runner.run("SELECT id, tree_level FROM ai_cards")).rows
    assert {row["id"]: row["tree_level"] for row in rows} == counted
    assert counted[chain[0].id] == CARD_TREE_DEPTH_MAX
