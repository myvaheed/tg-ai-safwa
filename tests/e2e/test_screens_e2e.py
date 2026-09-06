from __future__ import annotations

from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn
from review_e2e_helpers import (
    QueueTestCallback,
    QueueTestHistory,
    QueueTestMessage,
    review_services,
    standalone_tag_proposal,
)
from sqlalchemy import func, select
from ui_harness import spawn_timer

from safwa.bootstrap.modules import (
    FEATURE_CALLBACK_ACTIONS,
    FEATURE_TEXT_INPUTS,
    SCREENS,
)
from safwa.bootstrap.recovery import recover_startup
from safwa.features.tags.model import Tag
from safwa.foundation.workspace import Workspace
from telegram_llm import ChatHost
from tg_agent_shell.ai.runs import AgentRun
from tg_agent_shell.foundation.kinds import MARKS
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.proposals.model import (
    ChangeAction,
    ProposalChange,
)
from tg_agent_shell.proposals.telegram import render_ai_outcome, render_proposal
from tg_agent_shell.telegram import callback_token_handler, dismiss_prior_ui
from tg_agent_shell.telegram.model import CallbackToken
from tg_agent_shell.turn import TurnManager

pytestmark = pytest.mark.e2e


async def test_a_review_whose_screen_could_not_be_sent_does_not_stay_open(
    e2e_harness, monkeypatch
):
    """SC-FAIL-005 — tests/brd/tg_agent_shell/screens.feature"""
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    assert advisor.reviews.busy is True

    async def refuse(*_args, **_kwargs):
        raise RuntimeError("Telegram refused the screen")

    monkeypatch.setattr("tg_agent_shell.proposals.telegram.answer.render_proposal", refuse)
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )

    with pytest.raises(RuntimeError):
        await render_ai_outcome(message, services, outcome)

    # Nothing on screen and nothing waiting: this is exactly what `CueRuntime.can_speak`
    # reads, so a Reminder that comes due next is spoken instead of waiting for a restart.
    assert message.rendered == []
    assert advisor.reviews.busy is False
    async with e2e_harness.sessions() as session:
        claimed = await session.scalar(
            select(func.count(AgentRun.id)).where(AgentRun.claimed_at.is_not(None))
        )
    assert claimed == 0


async def test_restart_invalidates_an_unanswered_proposal_button(e2e_harness):
    """SC-BUTTON-003 — tests/brd/tg_agent_shell/screens.feature"""
    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == "proposal_approve")
        )
        assert token is not None
        token_value = token.token
        await recover_startup(session)
        await session.commit()
    # Restarting is what ends a review: nothing carries one across a process.
    e2e_harness.reviews.end_proposal(outcome.proposal_id)

    callback = QueueTestCallback(token_value, message)
    await callback_token_handler(callback, services)

    async with e2e_harness.sessions() as session:
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
        token = await session.get(CallbackToken, token_value)
    assert advisor.reviews.proposal(outcome.proposal_id) is None
    assert tag is None
    # The button went with the run of Safwa that drew it, and the screen it was on is
    # replaced rather than left standing with controls that answer nothing.
    assert token is None
    assert "out of date" in message.rendered[-1]
    assert message.markups[-1] is None
    assert not [alert for _text, alert in callback.answers if alert]
    assert len(provider.calls) == 1


async def test_a_button_works_once(e2e_harness):
    """SC-BUTTON-003 — tests/brd/tg_agent_shell/screens.feature"""
    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(("tag", {"mode": "create", "name": "VrWalk"})),
            "The VrWalk tag was saved.",
        ]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        token_value = await session.scalar(
            select(CallbackToken.token).where(CallbackToken.action == "proposal_approve")
        )

    await callback_token_handler(QueueTestCallback(token_value, message), services)
    second = QueueTestCallback(token_value, message)
    await callback_token_handler(second, services)

    async with e2e_harness.sessions() as session:
        tags = list(await session.scalars(select(Tag).where(Tag.name == "VrWalk")))
    # The work happened once, and the second press is told the action is spent rather
    # than the screen being replaced: the button is still there, it is just used up.
    assert len(tags) == 1
    assert [(text, alert) for text, alert in second.answers if alert] == [
        ("This action expired. Reopen the screen.", True)
    ]
    assert "out of date" not in message.rendered[-1]


async def test_navigating_away_freezes_the_proposal_into_the_same_outcome_text(e2e_harness):
    """SC-LIVE-001 — tests/brd/tg_agent_shell/screens.feature"""
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    proposal_id = await standalone_tag_proposal(e2e_harness, advisor, "VrWalk")
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, proposal_id)
    # A screen *below* the proposal: the old "older than this message" selector missed it.
    dashboard = SimpleNamespace(message_id=1, chat=message.chat, bot=message.bot)

    await dismiss_prior_ui(dashboard, services)

    async with e2e_harness.sessions() as session:
        proposal = advisor.reviews.proposal(proposal_id)
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
    assert proposal is None
    assert tag is None
    frozen = message.bot.edits[-1]
    assert "🗑 Discarded" in frozen
    assert "You continued the conversation without saving it." in frozen
    assert "New Tag “VrWalk”" in frozen


async def test_a_proposal_screen_lists_its_fields_behind_save_and_discard(e2e_harness):
    """PR-SCREEN-003 — tests/brd/tg_agent_shell/proposals.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release VrWalk", effort_points=3)
        await session.commit()
        card_id = card.id

    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("card", {"mode": "update", "id": card_id, "title": "Ship VrWalk"}))]
    )
    outcome = await advisor.handle("Rename the release card")
    assert outcome.proposal_id is not None
    message = QueueTestMessage()

    await render_proposal(message, review_services(e2e_harness, advisor), outcome.proposal_id)

    screen = message.rendered[-1]
    assert "Release VrWalk" in screen
    assert "Ship VrWalk" in screen
    assert message.buttons() == ["✅ Save", "🗑 Discard"]


async def test_a_proposal_holding_two_changes_lists_both_on_one_screen(e2e_harness):
    """PR-SCREEN-003 — tests/brd/tg_agent_shell/proposals.feature"""
    async with e2e_harness.sessions() as session:
        workspace = await session.get(Workspace, 1)
    proposal_id = e2e_harness.reviews.open_proposal(
        message="Two tags in one proposal",
        workspace_revision=workspace.revision,
        changes=[
            ProposalChange(entity="tag", action=ChangeAction.CREATE, values={"name": "VrWalk"}),
            ProposalChange(entity="tag", action=ChangeAction.CREATE, values={"name": "Release"}),
        ],
    ).id

    advisor, _provider = e2e_harness.advisor([])
    message = QueueTestMessage()

    await render_proposal(message, review_services(e2e_harness, advisor), proposal_id)

    screen = message.rendered[-1]
    assert "VrWalk" in screen
    assert "Release" in screen
    assert message.buttons() == ["✅ Save", "🗑 Discard"]
