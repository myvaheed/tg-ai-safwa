from __future__ import annotations

import pytest
from schedule_helpers import create_card
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.bootstrap.modules import PROPOSALS, REGISTRY
from safwa.features.cards.hooks import EFFORT_TRACKING_REMINDER_HOOK, effort_tracking_request
from safwa.features.cards.model import Card, CardStage
from safwa.features.cards.use_cases import (
    CARD_DONE,
    archive_subtree,
    delete_one_card,
    finish_action,
    finish_card,
    move_card,
    update_card_fields,
)
from safwa.features.profile.api import EFFORT_TRACKING_REMINDER, set_hook_switch
from safwa.features.profile.model import ProfileField
from safwa.features.profile.telegram import command_profile
from safwa.features.profile.use_cases import set_profile_field
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import OnCommitted
from tg_agent_shell.proposals.api import ApplyContext, ToolPreparationError
from tg_agent_shell.proposals.model import ProposalChange
from tg_agent_shell.proposals.prepare import ChangePreparer
from tg_agent_shell.telegram import callback_token_handler


async def test_the_question_names_only_finished_actions_still_without_an_estimate(sessions):
    """CD-EFFORT-044 — tests/brd/cards.feature"""
    assert EFFORT_TRACKING_REMINDER_HOOK in REGISTRY.hooks.specs
    assert EFFORT_TRACKING_REMINDER_HOOK.agent_related
    assert EFFORT_TRACKING_REMINDER_HOOK.on == (OnCommitted(kind=CARD_DONE),)
    async with sessions() as session:
        cards = [await create_card(session, kind="action", title=title) for title in (
            "Run", "Read", "Reopened", "Archived", "Deleted", "Estimated later",
        )]
        ids = [card.id for card in cards]
        for card in cards:
            await finish_action(session, card.id)
        await move_card(session, ids[2], CardStage.TODAY)
        await archive_subtree(session, ids[3])
        await delete_one_card(session, ids[4])
        await update_card_fields(session, ids[5], {"effort_points": 3})
        estimated = await create_card(session, kind="action", title="Already estimated", effort_points=0.5)
        await finish_action(session, estimated.id)
        opened = await create_card(session, kind="action", title="Still open")
        goal = await create_card(session, kind="goal", title="Empty Goal")
        await finish_card(session, goal.id)
        await session.commit()

        others = [*ids[2:], estimated.id, opened.id, goal.id]
        request = await effort_tracking_request(session, [*others, ids[1], ids[0]])
        assert request is not None
        assert f"- #{ids[0]} «Run»\n- #{ids[1]} «Read»" in request
        assert "effort_points on that exact Card, not on its open repeat" in request
        assert "Do not estimate without their answer" in request
        assert "If they do not know, leave it" in request
        for title in ("Reopened", "Archived", "Deleted", "Estimated later", "Already estimated", "Still open", "Empty Goal"):
            assert title not in request
        assert await effort_tracking_request(session, others) is None


@pytest.mark.parametrize("feature_on, hook_on", [(False, False), (False, True), (True, False), (True, True)])
async def test_the_question_follows_both_switches_independently_of_time_tracking(
    sessions, feature_on, hook_on,
):
    """CD-EFFORT-044 — tests/brd/cards.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Run")
        await finish_action(session, card.id)
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, feature_on)
        await set_profile_field(session, ProfileField.TIME_TRACKING, not feature_on)
        await set_hook_switch(session, EFFORT_TRACKING_REMINDER, on=hook_on)
        await session.commit()
    evaluated = [item.spec.name async for item in REGISTRY.hooks.evaluate(
        Committed(CARD_DONE, card.id), sessions
    )]
    expected = feature_on and hook_on
    assert (EFFORT_TRACKING_REMINDER in evaluated) is expected
    request = await REGISTRY.hooks.prepare(sessions, EFFORT_TRACKING_REMINDER, [card.id])
    assert (request is not None) is expected
    if expected:
        async with sessions() as session:
            await set_profile_field(session, ProfileField.EFFORT_TRACKING, False)
            await session.commit()
        assert await REGISTRY.hooks.prepare(sessions, EFFORT_TRACKING_REMINDER, [card.id]) is None


async def test_the_profile_shows_and_switches_the_reminder_only_with_effort_points_on(sessions):
    """CD-EFFORT-044 — tests/brd/cards.feature"""
    services = services_for(sessions)
    services.hooks = REGISTRY.hooks
    message = FakeMessage(7201, bot_message=True)

    async def press(markup, label):
        button = next(button for row in markup.inline_keyboard for button in row if button.text == label)
        bot_edits = len(message.bot.edits)
        await callback_token_handler(FakeCallback(button.callback_data.split(":", 1)[1], message), services)
        return message.bot.edits[-1][1:] if len(message.bot.edits) > bot_edits else message.edits[-1]

    async def hooks_menu():
        await command_profile(message, services)
        _, markup = await press(message.edits[-1][1], "🔔 Hooks")
        return markup

    assert not any("Effort Points reminder" in label for label in button_texts(await hooks_menu()))
    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await session.commit()
    markup = await hooks_menu()
    assert "🔔 Effort Points reminder: on" in button_texts(markup)
    text, markup = await press(markup, "🔔 Effort Points reminder: on")
    assert "After an Action is Done without an estimate" in text
    _, markup = await press(markup, "🔔 Effort Points reminder: on")
    assert "🔕 Effort Points reminder: off" in button_texts(markup)
    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, False)
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await session.commit()
    assert "🔕 Effort Points reminder: off" in button_texts(await hooks_menu())


@pytest.mark.parametrize("fields", [{"effort_points": 3}, {"effort_points": 3, "tracked_mins": 30}])
async def test_a_finished_repeat_accepts_its_estimate_but_refuses_other_edits(sessions, effort_on, fields):
    """CD-EFFORT-044 — tests/brd/cards.feature"""
    async with sessions() as session:
        run = await create_card(session, kind="action", title="Run", schedule="after completion")
        [successor_id] = (await finish_action(session, run.id)).successor_ids
        await session.commit()
        change = PROPOSALS.change_from_tool("card", {"mode": "update", "id": run.id, **fields})
        preparer = ChangePreparer(None, None, PROPOSALS)
        prepared = await preparer.prepare(session, change)
        await PROPOSALS.handler("card").apply(ApplyContext(session, frozenset()), ProposalChange(
            entity="card", action=change.action, entity_id=run.id,
            expected_version=run.version, values=prepared.values,
        ))
        assert run.effort_points == 3
        assert (await session.get(Card, successor_id)).effort_points is None
        change = PROPOSALS.change_from_tool("card", {"mode": "update", "id": run.id, "effort_points": 5, "title": "Run far"})
        with pytest.raises(ToolPreparationError) as refused:
            await preparer.prepare(session, change)
        assert refused.value.code == "closed_repeat"
