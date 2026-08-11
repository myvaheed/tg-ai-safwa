from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from safwa.ai.context import DialogueMessage
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.domain import create_card, create_check, pending_checks
from safwa.enums import CardStage, CheckOutcome
from safwa.models import CallbackToken, Card, ChangeProposal, Check, ProposalChange
from safwa.telegram import GenerationGuard, callback_token_handler, render_proposal

pytestmark = pytest.mark.e2e


def mutation_turn(*calls: tuple[str, dict[str, object]]) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(id=f"mutation-{index}", name=name, arguments=json.dumps(arguments))
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


class _TestMessage:
    def __init__(self) -> None:
        self.message_id = 900
        self.chat = SimpleNamespace(id=700, type="private")
        self.from_user = SimpleNamespace(id=42, is_bot=True)
        self.bot = _TestBot()
        self.text = ""
        self.rendered: list[str] = []

    async def edit_text(self, text, *, reply_markup=None, parse_mode=None):
        del reply_markup, parse_mode
        self.rendered.append(text)
        return self

    async def answer(self, text, *, reply_markup=None, parse_mode=None):
        del reply_markup, parse_mode
        self.rendered.append(text)
        return self


class _TestBot:
    def __init__(self) -> None:
        self.typing_calls = 0

    async def send_chat_action(self, *_args, **_kwargs) -> None:
        self.typing_calls += 1

    async def delete_message(self, *_args, **_kwargs) -> None:
        return None

    async def edit_message_reply_markup(self, *_args, **_kwargs) -> None:
        return None


class _TestCallback:
    def __init__(self, token: str, message: _TestMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message

    async def answer(self, text=None, *, show_alert=False) -> None:
        del text, show_alert


class _TestHistory:
    async def dialogue(self, _chat_id):
        return [DialogueMessage(role="user", content="[Initial request]: Close the market Card")]


def _services(harness, advisor) -> SimpleNamespace:
    return SimpleNamespace(
        sessions=harness.sessions,
        advisor=advisor,
        history=_TestHistory(),
        owner_id=42,
        guard=GenerationGuard(),
    )


async def _claim(harness, action: str, message: _TestMessage, services, **expected) -> None:
    """Press one live inline button.

    Re-rendering a screen mints fresh tokens for the same action, so a match on the
    action alone can pick up a superseded button from an earlier render.
    """
    async with harness.sessions() as session:
        tokens = list(
            await session.scalars(
                select(CallbackToken).where(
                    CallbackToken.action == action, CallbackToken.consumed_at.is_(None)
                )
            )
        )
    matching = [
        token
        for token in tokens
        if all(token.payload.get(key) == value for key, value in expected.items())
    ]
    assert matching, f"no live {action} button for {expected}"
    await callback_token_handler(_TestCallback(matching[-1].token, message), services)


async def _market_card_with_checks(harness) -> tuple[int, list[int]]:
    async with harness.sessions() as session:
        card = await create_card(
            session, title="Go to the market", kind="action", stage="today", effort_points=3
        )
        milk = await create_check(session, title="Milk", card_id=card.id)
        bread = await create_check(session, title="Bread", card_id=card.id)
        await session.commit()
        return card.id, [milk.id, bread.id]


async def test_completion_is_refused_while_checks_are_pending(e2e_harness):
    card_id, check_ids = await _market_card_with_checks(e2e_harness)
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("card", {"mode": "complete", "id": card_id})),
            "You still have Checks to answer on that Card.",
        ]
    )

    outcome = await advisor.handle("I finished the market run, close it")

    # The preparation error is model-visible, retryable, and carries the titles so the
    # model does not need a query_safwa round to discover them.
    tool_result = json.loads(provider.calls[-1][-1]["content"])
    assert tool_result["status"] == "error"
    assert tool_result["code"] == "pending_checks"
    assert tool_result["retryable"] is True
    assert "Milk" in tool_result["error"] and "Bread" in tool_result["error"]
    assert "resolve_for_card" in tool_result["hint"]
    assert outcome.proposal_id is None

    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).effective_stage == CardStage.TODAY.value
        assert len(await pending_checks(session, card_id)) == len(check_ids)


async def test_resolve_for_card_then_complete(e2e_harness):
    card_id, check_ids = await _market_card_with_checks(e2e_harness)
    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(("check", {"mode": "resolve_for_card", "card_id": card_id})),
            "Saved your answers.",
        ]
    )
    outcome = await advisor.handle("Let me answer the market Checks")
    assert outcome.proposal_id is not None

    message = _TestMessage()
    services = _services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)
    # Only the user can answer a Check, so this proposal screen carries field controls.
    assert "Tap a Check to change its answer" in message.rendered[-1]
    assert "Milk" in message.rendered[-1]

    await _claim(
        e2e_harness,
        "proposal_check_cycle",
        message,
        services,
        id=outcome.proposal_id,
        check_id=str(check_ids[0]),
    )
    async with e2e_harness.sessions() as session:
        change = await session.scalar(
            select(ProposalChange).where(ProposalChange.proposal_id == outcome.proposal_id)
        )
        # Rows start at `failed`; one tap advances to the next settable answer.
        assert change.values["outcomes"][str(check_ids[0])] == CheckOutcome.NOT_APPLICABLE.value
        assert change.values["outcomes"][str(check_ids[1])] == CheckOutcome.FAILED.value

    await _claim(e2e_harness, "proposal_approve", message, services, id=outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        assert (await session.get(ChangeProposal, outcome.proposal_id)).status == "approved"
        assert await pending_checks(session, card_id) == []
        first = await session.get(Check, check_ids[0])
        assert first.outcome == CheckOutcome.NOT_APPLICABLE.value
        assert first.resolved_by == "ai"
        assert first.resolved_at is not None

    # With nothing Pending the same completion the model was refused now prepares.
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("card", {"mode": "complete", "id": card_id})), "Closed."]
    )
    second = await advisor.handle("Now close it")
    assert second.proposal_id is not None
    services = _services(e2e_harness, advisor)
    await render_proposal(message, services, second.proposal_id)
    await _claim(e2e_harness, "proposal_approve", message, services, id=second.proposal_id)
    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).effective_stage == CardStage.DONE.value


async def test_resolve_for_card_is_refused_when_nothing_is_pending(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, title="Solo", kind="action", stage="today", effort_points=1
        )
        await session.commit()
        card_id = card.id

    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("check", {"mode": "resolve_for_card", "card_id": card_id})),
            "That Card has nothing to answer.",
        ]
    )
    await advisor.handle("Answer the Checks on Solo")
    tool_result = json.loads(provider.calls[-1][-1]["content"])
    assert tool_result["code"] == "no_pending_checks"


async def test_ai_can_create_and_read_checks(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, title="Posture", kind="action", stage="today", effort_points=1
        )
        await session.commit()
        card_id = card.id

    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(
                (
                    "check",
                    {
                        "mode": "create",
                        "title": "Posture straight?",
                        "card_id": card_id,
                        "repeatable": True,
                    },
                )
            ),
            "Added the Check.",
        ]
    )
    outcome = await advisor.handle("Track my posture on that Card")
    assert outcome.proposal_id is not None
    message = _TestMessage()
    services = _services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)
    await _claim(e2e_harness, "proposal_approve", message, services)

    async with e2e_harness.sessions() as session:
        created = await session.scalar(select(Check).where(Check.title == "Posture straight?"))
        assert created is not None
        assert created.repeatable is True
        assert created.outcome is None

    # ai_checks must be reachable, since it is the only route to a Check that has no
    # Card, Value, or Tag to hang off.
    rows = await advisor.query_runner.run(
        "SELECT id, title, status, repeatable FROM ai_checks ORDER BY id"
    )
    assert rows.rows[0]["status"] == "pending"
    assert rows.rows[0]["title"] == "Posture straight?"


async def test_manual_done_button_opens_the_resolution_screen(e2e_harness):
    card_id, check_ids = await _market_card_with_checks(e2e_harness)
    advisor, _provider = e2e_harness.advisor([])
    message = _TestMessage()
    services = _services(e2e_harness, advisor)

    from safwa.telegram.checks import render_check_resolution

    await render_check_resolution(
        message, services, card_id, back={"kind": "card", "id": card_id}
    )
    assert "Pending Checks" in message.rendered[-1]
    # Default is Missed, never Passed: a one-tap "all done" would fabricate history.
    assert message.rendered[-1].count("Missed") == len(check_ids)

    await _claim(
        e2e_harness, "check_resolve_cycle", message, services, check_id=check_ids[0]
    )
    await _claim(e2e_harness, "check_resolve_save", message, services, card_id=card_id)

    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).effective_stage == CardStage.DONE.value
        assert await pending_checks(session, card_id) == []
        assert (await session.get(Check, check_ids[0])).outcome == (
            CheckOutcome.NOT_APPLICABLE.value
        )
        assert (await session.get(Check, check_ids[1])).outcome == CheckOutcome.FAILED.value
