"""Effort Points are optional; screens and AI follow the owner's switch."""

from __future__ import annotations

from pathlib import Path

import pytest
from database_key import keyed
from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from safwa.bootstrap.modules import ALLOWED_VIEWS, PROPOSALS, REGISTRY
from safwa.features.cards.hooks import (
    TIME_TRACKING_REMINDER_HOOK,
    TODAY_OVERLOAD_HOOK,
    today_overload_request,
)
from safwa.features.cards.model import Card, CardStage
from safwa.features.cards.telegram import render_card, render_card_creation, render_dashboard
from safwa.features.cards.use_cases import (
    CARD_TODAY,
    create_card,
    finish_action,
    update_card_fields,
)
from safwa.features.planning.api import sprint_counts
from safwa.features.planning.model import SprintCommitment
from safwa.features.planning.telegram import render_plan, render_sprint
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.profile.api import (
    capacity_effort_points,
    effort_tracking_on,
    hook_switched_on,
    set_hook_switch,
)
from safwa.features.profile.model import ProfileField, UserProfile
from safwa.features.profile.telegram import command_profile
from safwa.features.profile.telegram.review import ProfileProposalPresenter
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.retro.analysis import overview_text, shares_text
from safwa.features.retro.records import aggregate, sprint_records, sprints_by_number
from safwa.features.retro.telegram import open_retro
from safwa.features.retro.use_cases import analysis_input
from tg_agent_shell.ai.sql import ReadOnlyQueryRunner
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_hook_cue
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.proposals.api import ApplyContext, ToolPreparationError
from tg_agent_shell.proposals.model import ProposalChange
from tg_agent_shell.proposals.prepare import ChangePreparer
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.model import UiSession


async def _press(message, services, markup, label):
    button = next(item for row in markup.inline_keyboard for item in row if item.text == label)
    bot_edits, message_edits = len(message.bot.edits), len(message.edits)
    await callback_token_handler(FakeCallback(button.callback_data.split(":", 1)[1], message), services)
    if len(message.bot.edits) > bot_edits:
        return message.bot.edits[-1][1:]
    if len(message.edits) > message_edits:
        return message.edits[-1]
    return None


async def test_ps_ep_021_the_switch_keeps_estimates_and_capacity(sessions):
    """PS-EP-021 — tests/brd/profile.feature"""
    services = services_for(sessions)
    services.hooks = REGISTRY.hooks
    async with sessions() as session:
        assert not await effort_tracking_on(session)
        card = await create_card(session, kind="action", title="A full day", effort_points=13)
        await set_profile_field(session, ProfileField.CAPACITY_EFFORT_POINTS, 20)
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        assert await capacity_effort_points(session) is None
        assert not await hook_switched_on(session, TODAY_OVERLOAD_HOOK.name)
        await session.commit()

    message = FakeMessage(7101, bot_message=True)
    await command_profile(message, services)
    text, markup = message.edits[-1]
    assert "🔢 Effort Points: off" in button_texts(markup)
    assert "🎯 Sprint capacity" not in button_texts(markup)
    assert "Sprint capacity:" not in text
    _, markup = await _press(message, services, markup, "🔢 Effort Points: off")
    assert "🎯 Sprint capacity" in button_texts(markup)
    async with sessions() as session:
        assert await capacity_effort_points(session) == 20
        assert await hook_switched_on(session, TODAY_OVERLOAD_HOOK.name)
        await add_hook_cue(session, hook=TODAY_OVERLOAD_HOOK.name, items=[card.id])
        await session.commit()
    await _press(message, services, markup, "🔢 Effort Points: on")
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        assert not profile.effort_tracking and profile.time_tracking
        assert profile.capacity_effort_points == 20
        assert (await session.get(Card, card.id)).effort_points == 13
        assert not await hook_switched_on(session, TODAY_OVERLOAD_HOOK.name)
    await render_card(message, services, card.id, full=True)
    text, markup = message.edits[-1]
    assert "Effort:" not in text and "🔢 Effort" not in button_texts(markup)
    assert "⌛ Time spent" in button_texts(markup)
    async with sessions() as session:
        change = PROPOSALS.change_from_tool("profile", {"mode": "update", "effort_tracking": True})
        prepared = await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
        proposal = ProposalChange(entity="profile", action=change.action, values=prepared.values)
        screen = await ProfileProposalPresenter().screen(session, [proposal])
        assert "Effort Points: off → on" in screen.diffs[0]
        await PROPOSALS.handler("profile").apply(ApplyContext(session, frozenset()), proposal)
        assert await effort_tracking_on(session)
        assert await hook_switched_on(session, TODAY_OVERLOAD_HOOK.name)


async def test_ps_ep_021_dependent_hooks_stop_before_evaluation_and_delivery(sessions):
    """PS-EP-021 — tests/brd/profile.feature"""
    hooks = REGISTRY.hooks
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Heavy", stage="today", effort_points=13)
        await create_card(session, kind="action", title="More", stage="today", effort_points=5)
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        await session.commit()
    event = Committed(CARD_TODAY, card.id)
    assert not [item async for item in hooks.evaluate(event, sessions)
                if item.spec.name == TODAY_OVERLOAD_HOOK.name]
    assert await hooks.prepare(sessions, TODAY_OVERLOAD_HOOK.name, [card.id]) is None
    assert await hooks.switched_on(sessions, TIME_TRACKING_REMINDER_HOOK)
    services = services_for(sessions)
    services.hooks = hooks
    message = FakeMessage(7102, bot_message=True)
    await command_profile(message, services)
    _, markup = await _press(message, services, message.edits[-1][1], "🔔 Hooks")
    assert not any("Today overload" in label for label in button_texts(markup))
    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await session.commit()
    assert [item.spec.name async for item in hooks.evaluate(event, sessions)
            if item.spec.name == TODAY_OVERLOAD_HOOK.name] == [TODAY_OVERLOAD_HOOK.name]
    assert "18 EP" in await hooks.prepare(sessions, TODAY_OVERLOAD_HOOK.name, [card.id])
    await command_profile(message, services)
    _, markup = await _press(message, services, message.edits[-1][1], "🔔 Hooks")
    assert "🔔 Today overload: on" in button_texts(markup)
    async with sessions() as session:
        await set_hook_switch(session, TODAY_OVERLOAD_HOOK.name, on=False)
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, False)
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await session.commit()
    assert not [item async for item in hooks.evaluate(event, sessions)
                if item.spec.name == TODAY_OVERLOAD_HOOK.name]
    async with sessions() as session:
        await set_hook_switch(session, TODAY_OVERLOAD_HOOK.name, on=True)
        await add_hook_cue(session, hook=TODAY_OVERLOAD_HOOK.name, items=[card.id])
        await add_hook_cue(session, hook=TIME_TRACKING_REMINDER_HOOK.name, items=[card.id])
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, False)
        await session.commit()
        pending = set(await session.scalars(select(Cue.hook)))
    assert {TODAY_OVERLOAD_HOOK.name, TIME_TRACKING_REMINDER_HOOK.name} <= pending
    assert await hooks.prepare(sessions, TODAY_OVERLOAD_HOOK.name, [card.id]) is None


@pytest.mark.parametrize("enabled", [False, True])
async def test_cd_effort_008_a_draft_and_a_proposal_save_without_an_estimate(sessions, enabled):
    """CD-EFFORT-008 — tests/brd/cards.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, enabled)
        session.add(UiSession(owner_id=42, kind="card_create", state={"title": "Run"}))
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(7111, bot_message=True)
    await render_card_creation(message, services)
    text, markup = message.edits[-1]
    assert "✅ Save" in button_texts(markup)
    assert ("🔢 Effort" in button_texts(markup)) is enabled
    await _press(message, services, markup, "✅ Save")
    async with sessions() as session:
        manual = await session.scalar(select(Card).where(Card.title == "Run"))
        assert manual.effort_points is None
        change = PROPOSALS.change_from_tool("card", {"mode": "create", "kind": "action", "title": "Walk"})
        prepared = await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
        proposal = ProposalChange(entity="card", action=change.action, values=prepared.values)
        result = await PROPOSALS.handler("card").apply(ApplyContext(session, frozenset()), proposal)
        assert result and (await session.get(Card, result[0])).effort_points is None
        await update_card_fields(session, manual.id, {"effort_points": 3})
        clearing = PROPOSALS.change_from_tool("card", {"mode": "update", "id": manual.id, "effort_points": None})
        if enabled:
            prepared = await ChangePreparer(None, None, PROPOSALS).prepare(session, clearing)
            assert "effort_points" in prepared.values and prepared.values["effort_points"] is None
            await PROPOSALS.handler("card").apply(ApplyContext(session, frozenset()), ProposalChange(
                entity="card", action=clearing.action, entity_id=manual.id,
                expected_version=manual.version, values=prepared.values,
            ))
        else:
            # While they are off a proposal carries no estimate, so one that was only that is refused.
            with pytest.raises(ToolPreparationError, match="Effort Points are off"):
                await ChangePreparer(None, None, PROPOSALS).prepare(session, clearing)
            await update_card_fields(session, manual.id, {"effort_points": None})
        assert manual.effort_points is None
        await update_card_fields(session, manual.id, {"schedule": "after completion"})
        from schedule_helpers import configure
        await configure(session, manual)
        [next_id] = (await finish_action(session, manual.id)).successor_ids
        assert (await session.get(Card, next_id)).effort_points is None


async def test_pl_ep_030_sprint_and_plan_count_actions_with_ep_off(sessions):
    """PL-EP-030 — tests/brd/planning.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Estimated", stage="sprint", effort_points=8)
        await create_card(session, kind="action", title="Unestimated", stage="sprint")
        await create_card(session, kind="action", title="Backlog")
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(7121, bot_message=True)
    await render_plan(message, services)
    text, markup = message.edits[-1]
    assert "2 Actions" in text and "EP" not in text and "capacity" not in text
    assert "Backlog" in button_texts(markup)
    async with sessions() as session:
        sprint = await start_sprint(session, success_criteria="Make progress")
        joined = await create_card(session, kind="action", title="Joined", stage="today")
        await finish_action(session, joined.id)
        assert await sprint_counts(session, sprint.id) == {
            "committed": 2, "added": 1, "removed": 0, "completed": 1, "unestimated": 2,
            "unknown_schedules": 0,
        }
        assert await today_overload_request(session, [joined.id]) is None
        await session.commit()
    await render_sprint(message, services)
    text, _ = message.edits[-1]
    assert "Taken <b>3 Actions</b>" in text and "Done <b>1 Actions</b>" in text
    assert "EP" not in text
    await render_dashboard(message, services, CardStage.BACKLOG, title="Backlog")
    assert "EP" not in message.edits[-1][0]
    await render_card(message, services, joined.id, full=True)
    assert "Effort:" not in message.edits[-1][0]


async def test_ps_ep_021_old_effort_buttons_cannot_write_after_switching_off(sessions):
    """PS-EP-021 — tests/brd/profile.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        card = await create_card(session, kind="action", title="Run", effort_points=3)
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(7122, bot_message=True)
    await render_card(message, services, card.id, full=True)
    _, choices = await _press(message, services, message.edits[-1][1], "🔢 Effort")
    await _press(message, services, choices, "No estimate")
    async with sessions() as session:
        assert (await session.get(Card, card.id)).effort_points is None
        await update_card_fields(session, card.id, {"effort_points": 5})
        await session.commit()
    await render_card(message, services, card.id, full=True)
    _, choices = await _press(message, services, message.edits[-1][1], "🔢 Effort")
    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, False)
        await session.commit()
    await _press(message, services, choices, "No estimate")
    async with sessions() as session:
        assert (await session.get(Card, card.id)).effort_points == 5


async def test_rt_ep_016_partial_estimates_are_kept_but_not_used_as_percentages(sessions):
    """RT-EP-016 — tests/brd/retro.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        estimated = await create_card(session, kind="action", title="Estimated", stage="sprint", effort_points=8)
        unestimated = await create_card(session, kind="action", title="Unestimated", stage="sprint")
        sprint = await start_sprint(session, success_criteria="Make progress")
        await finish_action(session, unestimated.id, tracked_mins=30)
        await finish_sprint(session)
        await session.commit()
        chosen = await sprints_by_number(session, [sprint.number])
        record = (await sprint_records(session, chosen))[sprint.number]
        assert record["actions_taken"] == 2 and record["actions_finished"] == 1
        assert "effort_taken" not in record and "capacity" not in record
        assert "done_share_percent" not in aggregate({sprint.number: record}, "sum")
        given = await analysis_input(session, sprint.id)
        assert not given.effort_tracking
        assert "EP" not in overview_text(given.sprints, effort_tracking=given.effort_tracking)
        assert "EP" not in shares_text(given.sprints, "Category", "Categories", "by_category", effort_tracking=given.effort_tracking)
    services = services_for(sessions)
    message = FakeMessage(7131, bot_message=True)
    await open_retro(message, services, sprint.id)
    text, _ = message.edits[-1]
    assert "Taken 2 Actions" in text and "Time" in text and "30m" in text
    assert "EP" not in text and "Effort" not in text
    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        # The Sprint's unknown estimate stays unknown after a later edit.
        await update_card_fields(session, unestimated.id, {"effort_points": 3})
        assert (await session.scalar(select(SprintCommitment).where(SprintCommitment.card_id == unestimated.id))).effort_snapshot is None
        assert (await session.get(Card, estimated.id)).effort_points == 8
        chosen = await sprints_by_number(session, [sprint.number])
        record = (await sprint_records(session, chosen))[sprint.number]
        assert record["unestimated_actions"] == 1 and "effort_taken" not in record
        await session.commit()
    await open_retro(message, services, sprint.id)
    text, _ = message.edits[-1]
    assert "8 EP" in text and "totals are partial" in text
    assert "EP an hour" not in text and "(0%)" not in text


def test_rt_ep_016_averages_exclude_incomplete_effort_and_report_the_sample():
    """RT-EP-016 — tests/brd/retro.feature"""
    records = {
        "full": {"link": "full", "effort_taken": 8, "effort_done": 4, "actions_taken": 2},
        "partial": {"link": "partial", "unestimated_actions": 1, "actions_taken": 3},
    }
    result = aggregate(records, "mean")
    assert result["values"]["effort_taken"] == 8
    assert result["values"]["actions_taken"] == 2.5
    assert result["done_share_percent"] == 50
    assert result["counted_over"]["effort_taken"] == 1
    assert result["counted_over"]["done_share_percent"] == 1


async def test_ps_ep_021_read_only_ai_views_follow_the_switch_without_erasing_estimates(sessions):
    """PS-EP-021 — tests/brd/profile.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Estimated", stage="sprint", effort_points=5)
        await start_sprint(session, success_criteria="Make progress")
        await session.commit()
        database = session.bind.url.database
    reader = ReadOnlyQueryRunner(keyed(Path(database)), ALLOWED_VIEWS)
    assert (await reader.run("SELECT effort_points FROM ai_cards")).rows[0]["effort_points"] is None
    metrics = (await reader.run("SELECT * FROM ai_current_sprint_metrics")).rows[0]
    assert metrics["committed"] is None and metrics["actions_committed"] == 1
    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await session.commit()
    assert (await reader.run("SELECT effort_points FROM ai_cards")).rows[0]["effort_points"] == 5
    assert (await reader.run("SELECT committed FROM ai_current_sprint_metrics")).rows[0]["committed"] == 5
