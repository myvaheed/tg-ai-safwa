from __future__ import annotations

import json

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn
from sqlalchemy import func, select

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import (
    PROPOSALS,
    SYSTEM_PROMPT,
)
from safwa.features.cards.model import Card, CardCategory, CardEnergyType, CardStage
from safwa.features.cards.use_cases import finish_action, move_card
from safwa.features.planning.use_cases import finish_sprint, sprint_metrics, start_sprint
from safwa.features.tags.model import CardTag, Tag
from safwa.features.values.model import CardValue, Value
from safwa.foundation.workspace import Workspace
from telegram_llm import DialogueMessage
from tg_agent_shell.ai.outcome import AIOutcome, AIOutcomeKind
from tg_agent_shell.ai.runs import AgentRun, AgentStep
from tg_agent_shell.proposals.model import (
    BatchDecision,
)
from tg_agent_shell.proposals.use_cases import approve_proposal
from tg_agent_shell.session import MAX_TOOL_CALLS

pytestmark = pytest.mark.e2e


async def test_placeholder_heavy_card_tool_payload_stays_a_root_action(e2e_harness):
    response = mutation_turn(
        (
            "card",
            {
                "mode": "create",
                "id": 0,
                "kind": "action",
                "title": "Подтянуться 20 раз",
                "note": "",
                "stage": "backlog",
                "priority": "medium",
                "hard_time": False,
                "blocked": False,
                "blocked_description": "",
                "effort_points": 1,
                "repeatable": False,
                "categories": ["self"],
                "energy_types": ["physical"],
                "value_id": 0,
                "value_ids": [],
                "value_query": "",
                "tag_id": 0,
                "tag_ids": [],
                "tag_query": "",
                "check_id": 0,
                "check_ids": [],
                "check_query": "",
                "parent_id": None,
                "parent_query": "",
            },
        )
    )
    advisor, _provider = e2e_harness.advisor([route_turn("workspace_mutator"), response])

    outcome = await advisor.handle("Сделай один Action: подтянуться 20 раз")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    change = e2e_harness.reviews.proposal(outcome.proposal_id).changes[0]
    assert change.values["title"] == "Подтянуться 20 раз"
    assert "parent_id" not in change.values
    assert "parent_query" not in change.values


async def test_ai_parent_query_rejects_non_ai_card_sql(
    e2e_harness,
):
    response = mutation_turn(
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Unsafe parent lookup",
                "parent_query": "SELECT id FROM cards WHERE title = 'Hidden table'",
                "effort_points": 2,
            },
        )
    )
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            response,
            "I could not safely resolve that parent, so nothing was proposed.",
            "I could not safely resolve that parent, so nothing was proposed.",
        ]
    )

    outcome = await advisor.handle("Create an action under that parent")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert len(provider.calls) == 4
    tool_result = json.loads(str(provider.calls[2][-1]["content"]))
    assert tool_result["code"] == "unsafe_query"
    assert "read-only SELECT over ai_cards" in tool_result["hint"]


async def test_ai_card_proposal_reaches_the_sprint_it_was_planned_into(e2e_harness):
    """PL-SCOPE-007 — tests/brd/planning.feature"""
    async with e2e_harness.sessions() as session:
        goal = await create_manual_card(
            session,
            title="To be fit",
            kind="goal",
            effort_points=None,
        )
        fitness = Value(name="Fitness", description="Build a healthy body", active=True)
        family = Tag(name="Family")
        session.add(fitness)
        session.add(family)
        await session.commit()

    response = mutation_turn(
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Push ups 30 times",
                "parent_query": "SELECT id FROM ai_cards WHERE title = 'To be fit'",
                "repeatable": True,
                "categories": ["self"],
                "energy_types": ["physical"],
                "value_query": "Fitness",
                "tag_query": "Family",
                "effort_points": 2,
            },
        )
    )
    advisor, provider = e2e_harness.advisor([route_turn("workspace_mutator"), response])
    outcome: AIOutcome = await advisor.handle(
        "Please create a new action Push ups 30 times and link it to To be fit goal"
    )

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    assert outcome.proposal_id is not None
    assert len(provider.calls) == 2
    assert not provider.responses

    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 1
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        action = await session.get(Card, affected[0])

        assert action is not None
        assert action.title == "Push ups 30 times"
        assert action.parent_id == goal.id
        assert action.repeatable is True
        assert action.effort_points == 2
        assert (
            await session.scalar(
                select(func.count(CardCategory.card_id)).where(CardCategory.card_id == action.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(CardEnergyType.card_id)).where(
                    CardEnergyType.card_id == action.id
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(CardValue.card_id)).where(CardValue.card_id == action.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(CardTag.card_id)).where(CardTag.card_id == action.id)
            )
            == 1
        )

        await move_card(session, action.id, CardStage.SPRINT)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        await move_card(session, action.id, CardStage.TODAY)
        completion = await finish_action(session, action.id)
        assert len(completion.successor_ids) == 1
        successor = await session.get(Card, completion.successor_ids[0])
        assert successor is not None
        assert successor.effective_stage == CardStage.TODAY.value
        assert successor.parent_id == goal.id
        current_goal = await session.get(Card, goal.id)
        assert current_goal is not None
        assert current_goal.effective_stage == CardStage.TODAY.value

        assert await sprint_metrics(session, sprint.id) == {
            "committed": 2,
            "added": 2,
            "removed": 0,
            "completed": 2,
        }

        await finish_sprint(session, reason="finished_early")
        workspace = await session.get(Workspace, 1)
        assert workspace is not None
        assert workspace.mode == "planning"
        assert successor.effective_stage == CardStage.TODAY.value
        await session.commit()


async def test_ai_read_query_round_trip_uses_safe_view(e2e_harness):
    async with e2e_harness.sessions() as session:
        action = await create_manual_card(
            session,
            title="Prepare release",
            stage="sprint",
            effort_points=5,
        )
        await start_sprint(session, success_criteria="Ship the release")
        await finish_action(session, action.id)
        await session.commit()

    query_response = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="read-1",
                name="query_data",
                arguments_json=json.dumps(
                    {"sql": "SELECT committed, completed FROM ai_current_sprint_metrics"}
                ),
            ),
        ),
    )
    answer_response = "You committed 5 effort points and completed all 5."
    advisor, provider = e2e_harness.advisor([query_response, answer_response])
    outcome = await advisor.handle(
        "How many effort points did I commit and complete in this sprint?"
    )

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == "You committed 5 effort points and completed all 5."
    assert len(provider.calls) == 2
    assert provider.calls[1][-2]["role"] == "assistant"
    assert provider.calls[1][-1]["role"] == "tool"
    follow_up_context = str(provider.calls[1][-1]["content"])
    assert '"committed": 5' in follow_up_context
    assert '"completed": 5' in follow_up_context

    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.created_at.desc()))
        assert run is not None
        assert run.status == "completed"
        step = await session.scalar(select(AgentStep).where(AgentStep.run_id == run.id))
        assert step is not None
        assert step.kind == "read_query"
        assert step.metadata_json["row_count"] == 1
        assert set(step.metadata_json["columns"]) == {"committed", "completed"}


async def test_a_turn_that_stops_without_words_is_asked_again(e2e_harness):
    """AG-ANSWER-013 — tests/brd/tg_agent_shell/agents.feature"""
    query_response = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="read-1",
                name="query_data",
                arguments_json=json.dumps({"sql": "SELECT id FROM ai_cards"}),
            ),
        ),
    )
    advisor, provider = e2e_harness.advisor(
        [query_response, "", "Isha is the night prayer."]
    )
    outcome = await advisor.handle("What is isha?")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == "Isha is the night prayer."
    assert len(provider.calls) == 3
    nudge = str(provider.calls[2][-1]["content"])
    assert nudge.startswith("[System]: You stopped without answering.")
    assert provider.calls[2][-2]["role"] == "tool"


async def test_a_turn_that_never_finds_words_still_reaches_the_owner(e2e_harness):
    """AG-ANSWER-013 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider = e2e_harness.advisor([""] * 6)
    outcome = await advisor.handle("What is isha?")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == "⚠️ There was nothing to say about that. You can ask again."
    assert len(provider.calls) == 6


async def test_read_and_mutation_in_one_turn_rejects_only_the_mutation(e2e_harness):
    """PR-WRITE-002 — tests/brd/tg_agent_shell/proposals.feature"""
    mixed = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="mixed-read",
                name="query_data",
                arguments_json=json.dumps({"sql": "SELECT id FROM ai_cards LIMIT 1"}),
            ),
            ProviderToolCall(
                id="mixed-write",
                name="card",
                arguments_json=json.dumps(
                    {"mode": "create", "kind": "goal", "title": "Be healthy"}
                ),
            ),
        ),
    )
    repaired = mutation_turn(
        ("card", {"mode": "create", "kind": "goal", "title": "Be healthy"}),
        prefix="after-read",
    )
    advisor, provider = e2e_harness.advisor([route_turn("workspace_mutator"), mixed, repaired])

    outcome = await advisor.handle("Inspect my cards, then create the unrelated health goal")

    assert outcome.proposal_id is not None
    tool_results = {
        item["name"]: json.loads(item["content"])
        for item in provider.calls[2]
        if item.get("role") == "tool"
    }
    assert isinstance(tool_results["query_data"], list)
    assert tool_results["card"]["code"] == "mixed_read_and_mutation_tools"
    assert tool_results["card"]["retryable"] is True


async def test_repeatable_action_preserves_tags_in_e2e_flow(e2e_harness):
    async with e2e_harness.sessions() as session:
        tag = Tag(name="Health")
        session.add(tag)
        action = await create_manual_card(
            session,
            title="Run outside",
            stage=CardStage.TODAY.value,
            effort_points=2,
            repeatable=True,
        )
        session.add(CardTag(card_id=action.id, tag_id=tag.id))
        await session.flush()
        completion = await finish_action(session, action.id)
        await session.commit()

    async with e2e_harness.sessions() as session:
        successor = await session.get(Card, completion.successor_ids[0])
        assert successor is not None
        copied_tag = await session.get(CardTag, {"card_id": successor.id, "tag_id": tag.id})
        assert copied_tag is not None


async def test_cacheable_prefix_is_byte_stable_across_turns(e2e_harness):
    dialogue = [DialogueMessage(role="user", content="[User]: What is next?")]
    advisor, provider = e2e_harness.advisor(["First.", "Second."])

    await advisor.handle("What is next?", dialogue=dialogue)
    await advisor.handle("What is next?", dialogue=dialogue)

    first, second = provider.calls
    # Only the trailing clock inside the final owner turn may differ.
    assert first[:-1] == second[:-1]
    assert "[System]: Current local time:" in str(first[-1]["content"])


async def test_cache_breakpoints_mark_exactly_the_stable_prefix(e2e_harness):
    dialogue = [
        DialogueMessage(role="user", content="[Initial request]: Plan this week."),
        DialogueMessage(role="assistant", content="What matters most?"),
        DialogueMessage(role="user", content="[User]: Health."),
    ]
    advisor, provider = e2e_harness.advisor(["Noted."], cache_breakpoints=True)

    await advisor.handle("Health.", dialogue=dialogue)

    messages = provider.calls[0]
    marked = [index for index, message in enumerate(messages) if isinstance(message["content"], list)]
    assert marked == [0, len(messages) - 2]
    assert messages[0]["content"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert isinstance(messages[-1]["content"], str)


async def test_cache_breakpoints_are_absent_by_default(e2e_harness):
    advisor, provider = e2e_harness.advisor(["Noted."])

    await advisor.handle("Hi", dialogue=[DialogueMessage(role="user", content="[User]: Hi")])

    assert all(isinstance(message["content"], str) for message in provider.calls[0])


async def test_advisor_combines_context_when_history_is_absent(e2e_harness):
    advisor, provider = e2e_harness.advisor(["Noted."])

    await advisor.handle("Hi")

    messages = provider.calls[0]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "Persistent memory:" in messages[-1]["content"]
    assert "Hi" in messages[-1]["content"]
    assert "[System]: Current local time:" in messages[-1]["content"]


async def test_advisor_sends_layered_system_blocks_and_canonical_dialogue(e2e_harness):
    response = "I remember the context."
    advisor, provider = e2e_harness.advisor([response])
    dialogue = [
        DialogueMessage(role="user", content="[Initial request]: Plan this week."),
        DialogueMessage(role="assistant", content="What matters most this week?"),
        DialogueMessage(
            role="user",
            content="[User]: Health and work.\n[User]: I also need time with family.",
        ),
    ]

    outcome = await advisor.handle("I also want a calmer evening.", dialogue=dialogue)

    assert outcome.kind is AIOutcomeKind.ANSWER
    messages = provider.calls[0]
    # Only messages[0] may be a system message; later blocks travel as owner text.
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[-1]["content"].startswith(dialogue[-1].content)
    assert "[System]: Current local time:" in messages[-1]["content"]
    timestamp = messages[-1]["content"].split("Current local time: ", 1)[1]
    assert "T" not in timestamp and timestamp.count(":") == 1
    system = str(messages[0]["content"])
    assert system == SYSTEM_PROMPT
    assert "query_data" in system
    assert "ai_cards(id, title" in system
    assert "Current local time" not in system
    assert "Current local time" not in str(messages[1]["content"])
    assert "Workspace revision:" not in system
    assert "Saved Requests:" not in system
    assert "Sprint cards:" not in system
    assert "Recent cards" not in system
    assert "Lexical card candidates" not in system
    tools = provider.options[0]["tools"]
    # The Advisor reads, shows and routes; every mutation tool belongs to the subagent that owns it.
    assert isinstance(tools, list) and [tool["function"]["name"] for tool in tools] == [
        "query_data",
        "open",
        "route",
    ]


async def test_mixed_query_and_mutation_resumes_only_after_approval(e2e_harness):
    """PR-QUEUE-007 — tests/brd/tg_agent_shell/proposals.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release VrWalk", effort_points=3)
        await session.commit()

    mixed_turn = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="create-tag",
                name="tag",
                arguments_json=json.dumps({"mode": "create", "name": "VrWalk"}),
            ),
            ProviderToolCall(
                id="recent-cards",
                name="query_data",
                arguments_json=json.dumps(
                    {"sql": "SELECT id, title FROM ai_cards ORDER BY created_at DESC LIMIT 10"}
                ),
            ),
        ),
    )
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mixed_turn,
            mutation_turn(("tag", {"mode": "create", "name": "VrWalk"})),
            "The tag was saved.",
            "The tag was saved.",
        ]
    )

    outcome = await advisor.handle("Create VrWalk and tag my recent cards")

    assert outcome.proposal_id is not None
    assert len(provider.calls) == 3
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()

    resumed = await advisor.resolve_approval(
        outcome.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    assert resumed is not None and resumed.kind is AIOutcomeKind.ANSWER
    assert "✅ Saved — New Tag “VrWalk”" in resumed.message
    assert "The tag was saved." in resumed.message
    assert len(provider.calls) == 5
    tool_messages = [message for message in provider.calls[2] if message["role"] == "tool"]
    assert [message["name"] for message in tool_messages] == ["tag", "query_data"]
    assert "mixed_read_and_mutation_tools" in str(tool_messages[0]["content"])
    assert f'"id": {card.id}' in str(tool_messages[1]["content"])
    approved_messages = [
        message
        for message in provider.calls[3]
        if message["role"] == "tool" and message["name"] == "tag"
    ]
    assert '"status": "approved"' in str(approved_messages[-1]["content"])

    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        batch = e2e_harness.reviews.batch_for_run(run.id)
        assert run.status == "completed"
        assert batch is None


async def test_resumed_request_replays_its_own_intermediate_steps(e2e_harness):
    """PR-RESULT-011 — tests/brd/tg_agent_shell/proposals.feature

    A multi-step request must keep every step it already took across each approval.
    """
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("card", {"mode": "create", "kind": "goal", "title": "Быть здоровым"})),
            ProviderTurn(
                content="",
                tool_calls=(
                    ProviderToolCall(
                        id="broken-read",
                        name="query_data",
                        arguments_json=json.dumps({"sql": "DELETE FROM cards"}),
                    ),
                ),
            ),
            mutation_turn(
                (
                    "card",
                    {
                        "mode": "create",
                        "kind": "action",
                        "title": "Подтянуться 20 раз",
                        "effort_points": 1,
                    },
                ),
                prefix="second",
            ),
            "Цель и задача готовы.",
            "Цель и задача готовы.",
        ]
    )

    first = await advisor.handle(
        "Сделай цель Быть здоровым и задачу подтянуться",
        dialogue=[
            DialogueMessage(
                role="user",
                content=(
                    "[Initial request]: Начнём\n"
                    "[User]: Сделай цель Быть здоровым и задачу подтянуться"
                ),
            )
        ],
    )
    assert first.proposal_id is not None
    async with e2e_harness.sessions() as session:
        goal_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()

    # No dialogue argument: the suspended turn resumes from what it persisted itself.
    second = await advisor.resolve_approval(
        first.proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": goal_ids}
    )
    assert second is not None and second.proposal_id is not None
    async with e2e_harness.sessions() as session:
        action_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, second.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        second.proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": action_ids}
    )

    assert final is not None and "Цель и задача готовы." in final.message
    assert len(provider.calls) == 6
    last = provider.calls[4]
    # The workspace session's context: its prompt, the workspace state, the conversation, then
    # every step it already took for this request.
    assert [message["role"] for message in last] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    # The request that started the turn is still the user message the model reads.
    assert "Сделай цель Быть здоровым и задачу подтянуться" in str(last[1]["content"])
    # Step 1: the saved Goal, described rather than reduced to an ID list.
    assert '"status": "approved"' in str(last[3]["content"])
    assert "Create Card “Быть здоровым”" in str(last[3]["content"])
    assert f'"affected_ids": {json.dumps(goal_ids)}' in str(last[3]["content"])
    assert "Do not propose it again" in str(last[3]["content"])
    # Step 2: the failed read is still visible, with a bounded instruction.
    assert last[4]["tool_calls"][0]["function"]["name"] == "query_data"
    assert '"code": "unsafe_query"' in str(last[5]["content"])
    assert "do not restart the request" in str(last[5]["content"])
    # Step 3: the steps speak for themselves, so no progress digest is restated on top.
    assert all("[Current request progress" not in str(message.get("content")) for message in last)
    assert "Create Card “Подтянуться 20 раз”" in str(last[7]["content"])


async def test_suspended_batch_persists_the_request_dialogue_and_transcript(e2e_harness):
    """AG-SESSION-008 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("tag", {"mode": "create", "name": "VrWalk"})),
        ]
    )
    outcome = await advisor.handle(
        "Create a VrWalk tag",
        dialogue=[DialogueMessage(role="user", content="[User]: Create a VrWalk tag")],
    )
    assert outcome.proposal_id is not None

    async with e2e_harness.sessions() as session:
        batch = next(iter(e2e_harness.reviews.open_batches), None)
        run = await session.get(AgentRun, batch.run_id)
        state = run.state_json
    # The batch holds the screens; the session holds what it needs to continue.
    assert batch is not None
    assert state["dialogue"] == [{"role": "user", "content": "[User]: Create a VrWalk tag"}]
    assert [message["role"] for message in state["transcript"]] == ["assistant", "tool"]
    assert state["transcript"][0]["tool_calls"][0]["function"]["name"] == "tag"


async def test_the_tool_call_budget_is_carried_across_an_approval(e2e_harness):
    """AG-BUDGET-011 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("tag", {"mode": "create", "name": "Budget"})),
            mutation_turn(("tag", {"mode": "create", "name": "Overrun"})),
        ]
    )
    outcome = await advisor.handle("Create a Budget tag")
    assert outcome.proposal_id is not None

    # The session has spent its whole budget; the approval must not hand it a fresh one.
    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        run.state_json = {**run.state_json, "tool_count": MAX_TOOL_CALLS}
        await session.commit()

    resumed = await advisor.resolve_approval(
        outcome.proposal_id,
        decision=BatchDecision.DISCARDED,
        result={},
    )

    assert resumed is not None
    assert "follow-up could not be generated" in resumed.message
    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        assert run.status == "failed"
        # The claim is released whichever way the turn ends, or the session is stuck.
        assert run.claimed_at is None


async def test_a_session_can_only_be_claimed_once(e2e_harness):
    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("tag", {"mode": "create", "name": "Claimed"})),
        ]
    )
    assert (await advisor.handle("Create a Claimed tag")).proposal_id is not None

    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        assert await advisor.store.claim_within(session, run.id) is not None
        assert await advisor.store.claim_within(session, run.id) is None


async def test_ending_a_session_ends_every_unfinished_one_below_it(e2e_harness):
    """AG-WORDS-020 — tests/brd/tg_agent_shell/agents.feature

    A subagent holds no `route`, so nothing routes three deep today. The store closes the
    branch whole anyway: the depth of a chain is its business, not its caller's.
    """
    advisor, _provider = e2e_harness.advisor(["Готово."])
    root = await advisor.store.create(kind="advisor")
    child = await advisor.store.create(kind="workspace_mutator", parent_run_id=root.id)
    grandchild = await advisor.store.create(kind="diary", parent_run_id=child.id)
    for run in (child, grandchild):
        await advisor.store.leave_interrupted(run.id, {}, "left unfinished")

    assert await advisor.store.close_unfinished_children(root.id) == 2

    async with e2e_harness.sessions() as session:
        status = {
            run.id: (run.status, run.claimed_at)
            for run in await session.scalars(select(AgentRun))
        }
    assert status[child.id] == ("abandoned", None)
    assert status[grandchild.id] == ("abandoned", None)
