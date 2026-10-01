from __future__ import annotations

import pytest
from hook_helpers import changes_of
from ui_harness import FakeCallback, FakeMessage, services_for

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.hooks import parent_completion_request
from safwa.features.cards.model import Card, CardStage
from safwa.features.cards.telegram import render_card
from safwa.features.cards.use_cases import (
    CARD_ACTIONS_FINISHED,
    create_card,
    finish_action,
    finish_card,
    move_card,
    reopen_card,
    toggle_card_check,
)
from safwa.features.checks.model import Check, CheckOutcome
from safwa.features.checks.use_cases import create_check
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.proposals.prepare import ChangePreparer
from tg_agent_shell.telegram import callback_token_handler


async def test_a_repeat_with_an_open_successor_does_not_offer_to_close_its_parent(sessions):
    """CD-CLOSE-042 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        empty = await create_card(session, kind="goal", title="Someday")
        repeat = await create_card(
            session,
            kind="action",
            title="Walk",
            effort_points=1,
            parent_id=goal.id,
            repeatable=True,
        )
        await finish_action(session, repeat.id)
        assert changes_of(session, CARD_ACTIONS_FINISHED) == []
        assert await parent_completion_request(session, [goal.id, empty.id]) is None


async def test_explicit_completion_and_reopen_keep_checks_and_child_state(sessions):
    """CD-STAGE-015 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(session, kind="subgoal", title="Move", parent_id=goal.id)
        action = await create_card(
            session, kind="action", title="Walk", effort_points=1, parent_id=subgoal.id
        )
        check = await create_check(session, title="Did it help?")
        await toggle_card_check(session, goal.id, check.id)
        await session.commit()
        with pytest.raises(DomainError, match="open Actions"):
            await finish_card(session, goal.id)
        change = PROPOSALS.change_from_tool("card", {"mode": "complete", "id": goal.id})
        with pytest.raises(DomainError, match="open Actions"):
            await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
        assert goal.completed_at is None
        await finish_action(session, action.id)
        with pytest.raises(DomainError, match="Check"):
            await finish_card(session, goal.id)
        await finish_card(session, goal.id, check_outcomes={check.id: CheckOutcome.PASSED})
        assert subgoal.effective_stage != "done"
        assert goal.completed_at is not None
        await reopen_card(session, goal.id)
        assert goal.completed_at is None
        assert action.effective_stage == "done"
        assert (await session.get(Check, check.id)).outcome is None
        await finish_card(session, goal.id, check_outcomes={check.id: CheckOutcome.PASSED})
        await finish_card(session, subgoal.id)
        await move_card(session, action.id, CardStage.TODAY)
        assert goal.effective_stage == subgoal.effective_stage == "today"
        assert goal.completed_at is subgoal.completed_at is None
        assert goal.manual_stage == subgoal.manual_stage == "backlog"
        assert (await session.get(Check, check.id)).outcome is None


async def test_a_goal_can_be_closed_and_reopened_on_its_screen(sessions):
    """CD-STAGE-015 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        action = await create_card(
            session, kind="action", title="Walk", effort_points=1, parent_id=goal.id
        )
        await finish_action(session, action.id)
        await session.commit()
        goal_id, action_id = goal.id, action.id
    services = services_for(sessions)
    message = FakeMessage(44, bot_message=True)

    async def click(label):
        await render_card(message, services, goal_id)
        markup = message.edits[-1][1]
        button = next(
            button for row in markup.inline_keyboard for button in row if button.text == label
        )
        await callback_token_handler(
            FakeCallback(button.callback_data.split(":", 1)[1], message), services
        )

    await click("✅ Done")
    async with sessions() as session:
        assert (await session.get(Card, goal_id)).effective_stage == "done"
    await click("♻️ Reopen")
    async with sessions() as session:
        assert (await session.get(Card, goal_id)).effective_stage == "backlog"
        assert (await session.get(Card, action_id)).effective_stage == "done"
