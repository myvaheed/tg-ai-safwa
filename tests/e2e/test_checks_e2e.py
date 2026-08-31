from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.ai.sql import ReadOnlyQueryRunner
from safwa.bootstrap.modules import (
    ALLOWED_VIEWS,
    FEATURE_CALLBACK_ACTIONS,
    FEATURE_TEXT_INPUTS,
    SCREENS,
)
from safwa.domain import (
    check_card_id,
    create_card,
    create_check,
    finish_action,
    pending_checks,
    resolve_check,
    toggle_card_check,
)
from safwa.enums import MessageKind
from safwa.features.cards.model import CardStage
from safwa.features.checks.model import CheckOutcome
from safwa.features.proposals.telegram import render_ai_outcome, render_proposal
from safwa.history import MARKS, TelegramNotes
from safwa.models import CallbackToken, Card, Check, TelegramMessage
from safwa.shell import callback_token_handler, command_start
from safwa.turn import TurnManager
from telegram_llm import ChatHost, DialogueMessage

pytestmark = pytest.mark.e2e


def mutation_turn(*calls: tuple[str, dict[str, object]]) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(id=f"mutation-{index}", name=name, arguments_json=json.dumps(arguments))
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


class _TestMessage:
    def __init__(self) -> None:
        self.message_id = 900
        self.chat = SimpleNamespace(id=700, type="private")
        self.from_user = SimpleNamespace(id=42, is_bot=True)
        self.bot = _TestBot(self)
        self.text = ""
        self.rendered: list[str] = []
        self.sent: list[str] = []
        self.deleted: list[int] = []
        self.markups: list[object] = []

    async def edit_text(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        return self

    async def answer(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.sent.append(text)
        self.markups.append(reply_markup)
        return self

    async def edit_reply_markup(self, *, reply_markup=None):
        self.markups.append(reply_markup)
        return self


class _TestBot:
    def __init__(self, message: _TestMessage) -> None:
        self.typing_calls = 0
        self.message = message

    async def send_chat_action(self, *_args, **_kwargs) -> None:
        self.typing_calls += 1

    async def delete_message(self, _chat_id, message_id) -> None:
        self.message.deleted.append(message_id)

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
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(harness.sessions), MARKS),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
        start_links=(),
        bot_username="safwa_ai_bot",
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
        milk = await create_check(session, title="Milk")
        bread = await create_check(session, title="Bread")
        for check in (milk, bread):
            await toggle_card_check(session, card.id, check.id)
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
    assert "[title](check:<id>)" in tool_result["hint"]
    assert outcome.proposal_id is None

    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).effective_stage == CardStage.TODAY.value
        assert len(await pending_checks(session, card_id)) == len(check_ids)


async def test_answering_checks_by_proposal_then_completing(e2e_harness):
    card_id, check_ids = await _market_card_with_checks(e2e_harness)
    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("check", {"mode": "complete", "id": check_ids[0]}),
                ("check", {"mode": "cancel", "id": check_ids[1]}),
            ),
            "Saved your answers.",
        ]
    )
    outcome = await advisor.handle("I got the milk but they had no bread")
    assert outcome.proposal_id is not None

    message = _TestMessage()
    services = _services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)
    # A Check proposal reads like every other item: a diff plus Save/Discard, no field controls.
    assert "Answer Check · AI proposal" in message.rendered[-1]
    assert "Status: Passed" in message.rendered[-1]
    assert (await _live_actions(e2e_harness)) == {"proposal_approve", "proposal_reject"}

    await _claim(e2e_harness, "proposal_approve", message, services, id=outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        assert advisor.reviews.proposal(outcome.proposal_id) is None
        first = await session.get(Check, check_ids[0])
        assert first.outcome == CheckOutcome.PASSED.value
        assert first.resolved_by == "ai"
        assert first.resolved_at is not None
    # The second proposal of the batch is queued behind the first.
    await _claim(e2e_harness, "proposal_approve", message, services)
    async with e2e_harness.sessions() as session:
        assert (await session.get(Check, check_ids[1])).outcome == CheckOutcome.MISSED.value
        assert await pending_checks(session, card_id) == []

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


async def test_a_cited_item_opens_its_manual_screen(e2e_harness):
    card_id, check_ids = await _market_card_with_checks(e2e_harness)
    advisor, _provider = e2e_harness.advisor(
        [f"Answer the [Milk](check:{check_ids[0]}) Check when you get home."]
    )
    outcome = await advisor.handle("Which Checks are still open on the market run?")
    assert outcome.proposal_id is None

    message = _TestMessage()
    services = _services(e2e_harness, advisor)
    await render_ai_outcome(message, services, outcome)
    # The citation becomes a deep link inside ordinary dialogue; nothing is rendered yet.
    assert (
        f'<a href="https://t.me/safwa_ai_bot?start=check-{check_ids[0]}">Milk</a>'
        in message.rendered[-1]
    )
    assert "<b>Check</b>" not in message.rendered[-1]
    async with e2e_harness.sessions() as session:
        stored = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == message.message_id)
        )
        assert stored.kind == MessageKind.DIALOGUE_ASSISTANT.value

    # Tapping the link sends /start with the payload, and the screen arrives as its own
    # message: the reply above it is canonical history and must survive.
    message.text = f"/start check-{check_ids[0]}"
    await command_start(message, services)
    assert "<b>Check</b>: Milk" in message.sent[-1]
    assert "Go to the market" in message.sent[-1]
    assert {"check_toggle_repeat", "check_set_status"} <= await _live_actions(e2e_harness)

    await _claim(
        e2e_harness, "check_set_status", message, services, id=check_ids[0], outcome="passed"
    )
    async with e2e_harness.sessions() as session:
        answered = await session.get(Check, check_ids[0])
        assert answered.outcome == CheckOutcome.PASSED.value
        assert answered.resolved_by == "user_ui"
        assert [item.id for item in await pending_checks(session, card_id)] == [check_ids[1]]


async def test_a_closed_repeat_is_marked_everywhere_it_is_read(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_card(session, title="Posture", kind="action", effort_points=1)
        first = await create_check(session, title="Posture straight?", repeatable=True)
        await toggle_card_check(session, card.id, first.id)
        await session.commit()
        _, second = await resolve_check(session, first.id, CheckOutcome.PASSED)
        run = await create_card(
            session, title="Run", kind="action", stage="today", effort_points=1, repeatable=True
        )
        closed_run = await finish_action(session, run.id, CardStage.DONE)
        await session.commit()
        first_id, second_id = first.id, second.id
        run_id, live_run_id = run.id, closed_run.successor_ids[0]

    # The model reads the marker: a closed instance names itself, the open one does not.
    runner = ReadOnlyQueryRunner(e2e_harness.database_path, ALLOWED_VIEWS)
    titles = {
        row["id"]: row["title"]
        for row in (await runner.run("SELECT id, title FROM ai_checks")).rows
    }
    assert titles[first_id] == f"Posture straight? [🔄1, live #{second_id}]"
    assert titles[second_id] == "Posture straight?"
    cards = {
        row["id"]: row["title"]
        for row in (await runner.run("SELECT id, title FROM ai_cards")).rows
    }
    assert cards[run_id] == f"Run [🔄1, live #{live_run_id}]"
    assert cards[live_run_id] == "Run"

    # `open` shows exactly the id it was given: the marker is what says which one that is.
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("open", {"item_type": "check", "id": first_id})), "Here it is."]
    )
    outcome = await advisor.handle("Show me the Check I already answered")
    assert outcome.open_item == f"check-{first_id}"

    message = _TestMessage()
    services = _services(e2e_harness, advisor)
    await render_ai_outcome(message, services, outcome)
    assert f"<b>Check</b>: Posture straight? [🔄1, live #{second_id}]" in message.sent[-1]
    # The closed screen offers the live instance the series moved to.
    await _claim(e2e_harness, "check_view", message, services, id=second_id)
    assert "Status: ⬜ Pending" in message.rendered[-1]

    # A citation of the closed instance carries the marker too, so the link cannot pass for
    # the open one.
    advisor, _provider = e2e_harness.advisor([f"Yesterday's [x](check:{first_id}) passed."])
    cited = await advisor.handle("How did it go yesterday?")
    await render_ai_outcome(message, _services(e2e_harness, advisor), cited)
    assert (
        f'?start=check-{first_id}">Posture straight? [🔄1, live #{second_id}]</a>'
        in message.rendered[-1]
    )


async def test_citations_link_live_items_and_drop_missing_ones(e2e_harness):
    card_id, check_ids = await _market_card_with_checks(e2e_harness)
    advisor, _provider = e2e_harness.advisor(
        [
            f"Errand [Go to the market](card:{card_id}) still needs "
            f"[Milk](check:{check_ids[0]}), and [that old one](card:4242) is gone."
        ]
    )
    outcome = await advisor.handle("Show me today's errands")

    message = _TestMessage()
    services = _services(e2e_harness, advisor)
    await render_ai_outcome(message, services, outcome)
    text = message.rendered[-1]
    assert f'?start=card-{card_id}">⭐️ Go to the market · ⚡3</a>' in text
    assert f'?start=check-{check_ids[0]}">Milk</a>' in text
    # An item that no longer exists keeps its words and loses its link: the reply stays in
    # the chat for good, so a dead link would outlive every retry.
    assert "that old one" in text
    assert "card-4242" not in text

    message.text = f"/start card-{card_id}"
    await command_start(message, services)
    assert "Go to the market" in message.sent[-1]

    # A link that outlives its item reports the failure and changes nothing else.
    message.text = "/start card-4242"
    await command_start(message, services)
    assert "⚠️ Error while opening: Card does not exist" in message.sent[-1]

    message.text = "/start nonsense"
    await command_start(message, services)
    assert "not a Safwa item" in message.sent[-1]


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
                ("check", {"mode": "create", "title": "Posture straight?", "repeatable": True})
            ),
            "Added the Check.",
        ]
    )
    outcome = await advisor.handle("Track my posture")
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
        # The check tool never attaches; a new Check starts unlinked.
        assert await check_card_id(session, created.id) is None
        created_id = created.id

    # ai_checks must be reachable, since it is the only route to a Check with no Card.
    rows = await advisor.adapters.query_runner.run(
        "SELECT id, title, status, repeatable FROM ai_checks ORDER BY id"
    )
    assert rows.rows[0]["status"] == "pending"
    assert rows.rows[0]["title"] == "Posture straight?"

    # A second turn attaches it, exactly the way a Value or Tag is attached.
    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(("card", {"mode": "link", "id": card_id, "check_ids": [created_id]})),
            "Attached it.",
        ]
    )
    outcome = await advisor.handle("Put that Check on the posture Card")
    assert outcome.proposal_id is not None
    services = _services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)
    await _claim(e2e_harness, "proposal_approve", message, services, id=outcome.proposal_id)

    async with e2e_harness.sessions() as session:
        assert await check_card_id(session, created_id) == card_id

    # One place answers which Checks are on a Card, so the same fact is never counted
    # twice in two places that can disagree.
    standing = await advisor.adapters.query_runner.run(
        f"SELECT title, status FROM ai_checks WHERE card_id = {card_id}"
    )
    assert standing.rows == [{"title": "Posture straight?", "status": "pending"}]


async def test_ai_links_a_check_to_a_card_by_title(e2e_harness):
    """CH-LINK-003 — tests/brd/checks.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, title="Go to the pharmacy", kind="action", stage="today", effort_points=1
        )
        loose = await create_check(session, title="Take the tote bag")
        await session.commit()
        card_id, check_id = card.id, loose.id

    # check_query resolves an exact title, the same way value_query and tag_query do, so
    # the model can attach a Check it has only seen by name.
    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("card", {"mode": "link", "id": card_id, "check_query": ["Take the tote bag"]})
            ),
            "Linked it.",
        ]
    )
    outcome = await advisor.handle("The tote bag goes on the pharmacy run")
    assert outcome.proposal_id is not None
    message = _TestMessage()
    services = _services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)
    await _claim(e2e_harness, "proposal_approve", message, services, id=outcome.proposal_id)

    async with e2e_harness.sessions() as session:
        assert await check_card_id(session, check_id) == card_id
        assert [item.id for item in await pending_checks(session, card_id)] == [check_id]


async def _live_actions(harness) -> set[str]:
    async with harness.sessions() as session:
        return set(
            await session.scalars(
                select(CallbackToken.action).where(CallbackToken.consumed_at.is_(None))
            )
        )


async def test_manual_check_screens_only_repeat_and_answer(e2e_harness):
    card_id, check_ids = await _market_card_with_checks(e2e_harness)
    advisor, _provider = e2e_harness.advisor([])
    message = _TestMessage()
    services = _services(e2e_harness, advisor)

    from safwa.features.checks.telegram import render_check, render_checks

    back = {"kind": "card", "id": card_id}
    await render_checks(message, services, card_id, back=back)
    # The manual screens answer a Check; every other Check action is proposal-only.
    listed = await _live_actions(e2e_harness)
    assert listed == {"check_view", "check_back"}

    await render_check(message, services, check_ids[0], card_id=card_id, back=back)
    assert (await _live_actions(e2e_harness)) - listed == {
        "check_toggle_repeat",
        "check_set_status",
        "check_choose_values",
        "check_list",
        "check_delete_prompt",
    }
    assert "Note" not in message.rendered[-1]

    await _claim(
        e2e_harness, "check_set_status", message, services, id=check_ids[0], outcome="passed"
    )
    async with e2e_harness.sessions() as session:
        answered = await session.get(Check, check_ids[0])
        assert answered.outcome == CheckOutcome.PASSED.value
        assert answered.resolved_by == "user_ui"
        assert [item.id for item in await pending_checks(session, card_id)] == [check_ids[1]]


async def test_manual_done_button_opens_the_resolution_screen(e2e_harness):
    card_id, check_ids = await _market_card_with_checks(e2e_harness)
    advisor, _provider = e2e_harness.advisor([])
    message = _TestMessage()
    services = _services(e2e_harness, advisor)

    from safwa.features.cards.telegram import render_check_resolution

    await render_check_resolution(
        message, services, card_id, back={"action": "card_view", "id": card_id}
    )
    assert "Pending Checks" in message.rendered[-1]
    # Nothing is prefilled.
    assert message.rendered[-1].count("Pending") == len(check_ids) + 1

    await _claim(
        e2e_harness,
        "check_resolve_set",
        message,
        services,
        check_id=check_ids[0],
        outcome="passed",
    )
    # Saving with a row still unanswered changes nothing and says so.
    await _claim(e2e_harness, "check_resolve_save", message, services, card_id=card_id)
    assert "Answer every Check before saving." in message.rendered[-1]
    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).effective_stage == CardStage.TODAY.value

    await _claim(
        e2e_harness,
        "check_resolve_set",
        message,
        services,
        check_id=check_ids[1],
        outcome="missed",
    )
    await _claim(e2e_harness, "check_resolve_save", message, services, card_id=card_id)

    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).effective_stage == CardStage.DONE.value
        assert await pending_checks(session, card_id) == []
        assert (await session.get(Check, check_ids[0])).outcome == CheckOutcome.PASSED.value
        assert (await session.get(Check, check_ids[1])).outcome == CheckOutcome.MISSED.value
