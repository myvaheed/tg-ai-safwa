from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from sqlalchemy import select

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import PROPOSALS, routed_prompt
from safwa.features.cards.model import Card, CardKind, CardStage
from safwa.features.cards.use_cases import create_card, finish_action
from safwa.features.diary.agent import DIARY_AGENT, day_read_tool, diary_clock
from safwa.features.diary.model import DiaryEntry
from telegram_llm import DialogueMessage
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.ai.runs import AgentRun, AgentStep
from tg_agent_shell.ai.subagents import RoutedSubagent
from tg_agent_shell.ai.tools import IMMEDIATE_TOOLS
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.proposals.use_cases import approve_proposal

TODAY = date.today().isoformat()

pytestmark = pytest.mark.e2e


class StubDayReader:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    async def day_transcript(self, _chat_id: int, *, start, end, token_budget) -> str:  # noqa: ARG002
        return self.transcript


def turn(*calls: tuple[str, dict[str, object]], prefix: str = "call") -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(
                id=f"{prefix}-{index}", name=name, arguments_json=json.dumps(arguments)
            )
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


def review_turn(reason: str) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="review-autoapprove",
                name="autoapprove",
                arguments_json=json.dumps({"reason": reason}),
            ),
        ),
    )


def route_receipts(provider) -> list[dict]:
    """Every `route` result the Advisor was handed, in the order it read them."""
    seen: list[dict] = []
    for call in provider.calls:
        for item in call:
            if item.get("role") == "tool" and item.get("name") == "route":
                receipt = json.loads(str(item["content"]))
                if receipt not in seen:
                    seen.append(receipt)
    return seen


def diary_subagent(
    harness, transcript: str = "[08:40] [User]: Долгий день, но рынок закрыл."
) -> RoutedSubagent:
    """The real Diary subagent: the same session shape the Advisor runs on."""
    return RoutedSubagent(
        name="diary",
        prompt=routed_prompt(DIARY_AGENT),
        read_tools=(
            day_read_tool(
                StubDayReader(transcript), chat_id=42, timezone="Europe/Istanbul"
            ),
        ),
        mutation_tools=("diary",),
        clock=lambda: diary_clock("Europe/Istanbul"),
    )


async def test_a_routed_subagent_hands_its_words_back_and_the_advisor_speaks(e2e_harness):
    """AG-RECEIPT-006 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_day", {}), prefix="diary"),
            "Записал тот день: [08.03.2026](diary:4).",
            "Готово — [08.03.2026](diary:4).",
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Запиши восьмое в дневник")

    # The owner reads the Advisor; the subagent's sentence reached it as a receipt.
    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == "Готово — [08.03.2026](diary:4)."
    # It read under its own prompt, not the Advisor's.
    assert str(provider.calls[1][0]["content"]).startswith("# Safwa")
    assert "You keep the user's Diary" in str(provider.calls[1][0]["content"])
    # The route call was answered in place, so the Advisor resumed where it left off.
    handed_back = json.loads(
        next(item for item in provider.calls[3] if item.get("role") == "tool")["content"]
    )
    assert handed_back == {
        "subagent": "diary",
        "outcome": "done",
        "did": [],
        "text": "Записал тот день: [08.03.2026](diary:4).",
    }
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
        kinds = [
            step.kind for step in await session.scalars(select(AgentStep).order_by(AgentStep.id))
        ]
    assert [(run.kind, run.status, run.parent_run_id) for run in runs] == [
        ("advisor", "completed", None),
        ("diary", "completed", 1),
    ]
    assert kinds == ["route", "read"]


async def test_an_autoapproved_board_route_hands_back_its_receipt(e2e_harness):
    """AG-RECEIPT-006 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, kind=CardKind.ACTION, title="Купить молоко", effort_points=1
        )
        await session.commit()

    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "workspace_mutator"})),
            turn(
                ("card", {"mode": "update", "id": card.id, "title": "Купить овсяное молоко"}),
                prefix="workspace_mutator",
            ),
            review_turn("The operation and every non-default value are explicit."),
            "Переименовал чек.",
            "Готово.",
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle("Переименуй Купить молоко в Купить овсяное молоко")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert [message["role"] for message in provider.calls[1]] == ["system", "user"]
    receipt = next(receipt for receipt in route_receipts(provider) if receipt["subagent"] == "workspace_mutator")
    assert receipt["did"] == [
        "⚡ Auto-saved — Edit Action “Купить овсяное молоко” "
        "(Title: Купить молоко → Купить овсяное молоко)"
    ]
    assert receipt["text"] == "Переименовал чек."
    # Five turns are scripted and five are made. An automatic Save resolves a screen that is
    # already suspended, so resuming the workspace session and then the Advisor is the only way
    # either of them runs again — not a second pass through the turn that opened it.
    assert provider.total == 5
    async with e2e_harness.sessions() as session:
        stored = await session.get(Card, card.id)
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
    assert stored is not None and stored.title == "Купить овсяное молоко"
    assert [(run.kind, run.status, run.parent_run_id) for run in runs] == [
        ("advisor", "completed", None),
        ("workspace_mutator", "completed", 1),
    ]


async def test_a_routed_subagent_proposes_for_itself(e2e_harness):
    """AG-ROUTE-001 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, kind=CardKind.ACTION, title="Сходить на рынок", effort_points=2
        )
        await finish_action(session, card.id, CardStage.DONE)
        await session.commit()

    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(
                ("read_day", {}),
                ("query_data", {"sql": "SELECT card_id, operation FROM ai_card_events"}),
                prefix="diary",
            ),
            turn(
                (
                    "diary",
                    {
                        "mode": "update",
                        "date": TODAY,
                        "pov": "Закрыл рынок, хоть и поздно.",
                        "remark": "One thing finished is still a finished day.",
                        "feeling_score": 6,
                    },
                ),
                prefix="diary",
            ),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    # The Advisor's own context never carried the day: it handed the turn over first.
    assert "Закрыл рынок" not in json.dumps(provider.calls[0], ensure_ascii=False)
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
    batch = next(iter(e2e_harness.reviews.open_batches), None)
    # The Diary session waits for the screen, and the Advisor waits for the Diary.
    assert [(run.kind, run.status, run.parent_run_id) for run in runs] == [
        ("advisor", "awaiting_approval", None),
        ("diary", "awaiting_approval", 1),
    ]
    # The Advisor stored the route call it has not answered yet.
    assert runs[0].state_json["awaiting_route"]["subagent"] == "diary"
    assert batch.run_id == runs[1].id
    # The draft lives on the Diary session's own row, ready for a correction to reach it.
    written = [
        json.loads(tool_call["function"]["arguments"])
        for message in runs[1].state_json["transcript"]
        for tool_call in message.get("tool_calls") or []
        if tool_call["function"]["name"] == "diary"
    ]
    assert written[0]["pov"] == "Закрыл рынок, хоть и поздно."


async def test_an_unknown_route_target_is_repaired_in_the_next_response(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "journal"})),
            turn(("route", {"name": "diary"})),
            "Записал.",
            "Готово.",
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.message == "Готово."
    rejected = json.loads(
        next(item for item in provider.calls[1] if item.get("role") == "tool")["content"]
    )
    assert rejected["code"] == "unknown_subagent"
    assert rejected["retryable"] is True


async def test_a_routed_subagent_is_offered_only_its_own_tools(e2e_harness):
    """AG-ROUTE-004 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), "Записал.", "Готово."],
        subagents=(diary_subagent(e2e_harness),),
    )

    await advisor.handle("Запиши сегодняшний день")

    offered = {tool["function"]["name"] for tool in provider.options[1]["tools"]}
    assert offered == {"read_day", "query_data", "diary"}
    # No recursion, and no reach into the workspace.
    assert "route" not in offered
    assert "card" not in offered


async def test_the_board_owns_every_mutation_tool(e2e_harness):
    """PR-WRITE-002 — tests/brd/tg_agent_shell/proposals.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "workspace_mutator"})),
            turn(("card", {"mode": "create", "kind": "goal", "title": "Быть здоровым"})),
        ]
    )

    outcome = await advisor.handle("Сделай цель Быть здоровым")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    # The Advisor has no way to describe a change instead of routing it: it has no tool.
    advisor_tools = {tool["function"]["name"] for tool in provider.options[0]["tools"]}
    assert advisor_tools == {"query_data", "open", "route"}
    board_tools = {tool["function"]["name"] for tool in provider.options[1]["tools"]}
    assert board_tools == {
        "query_data",
        "card",
        "check",
        "value",
        "tag",
        "request",
        "reminder",
        "remove",
    }
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
    assert [(run.kind, run.status) for run in runs] == [
        ("advisor", "awaiting_approval"),
        ("workspace_mutator", "awaiting_approval"),
    ]


async def test_a_subagent_reads_the_tail_of_the_conversation_as_tagged_data(e2e_harness):
    """AG-ROUTE-002 — tests/brd/tg_agent_shell/agents.feature"""
    dialogue = [
        DialogueMessage(role="user", content=f"[User]: сообщение {index}")
        if index % 2 == 0
        else DialogueMessage(role="assistant", content=f"ответ {index}")
        for index in range(12)
    ]
    advisor, provider = e2e_harness.advisor(
        [turn(("tag", {"mode": "create", "name": "VrWalk"}))],
        subagents=(e2e_harness.workspace(),),
    )

    await advisor.handle("Заведи тег VrWalk", dialogue=dialogue)

    board_seen = [item for item in provider.calls[0] if item["role"] == "user"]
    assert len(board_seen) == 1
    conversation = str(board_seen[0]["content"])
    assert conversation.startswith("[System]: Current workspace state:")
    # The tail only, and every line says whose it is: the subagent said none of it, so
    # nothing reaches it in the slot it writes to itself.
    assert "сообщение 0" not in conversation
    assert "<User>сообщение 2</User>" in conversation
    assert "<Advisor>ответ 11</Advisor>" in conversation
    assert not [item for item in provider.calls[0] if item["role"] == "assistant"]


async def test_a_subagent_is_required_to_open_with_a_tool_call(e2e_harness):
    """AG-ANSWER-014 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider = e2e_harness.advisor(
        [turn(("tag", {"mode": "create", "name": "VrWalk"}))],
        subagents=(e2e_harness.workspace(),),
    )

    await advisor.handle("Заведи тег VrWalk")

    # The Advisor may answer in words; the session routed to for the work may not.
    assert provider.options[0]["tool_choice"] == "required"


async def test_route_cannot_share_its_response_with_another_call(e2e_harness):
    """AG-ROUTE-005 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"}), ("query_data", {"sql": "SELECT 1"})),
            turn(("route", {"name": "diary"})),
            "Записал.",
            "Готово.",
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.message == "Готово."
    refused = [
        json.loads(str(item["content"]))
        for item in provider.calls[1]
        if item.get("role") == "tool"
    ]
    # Neither call ran: a suspended response cannot carry a result for its sibling.
    assert {entry["code"] for entry in refused} == {"route_is_not_shared"}
    assert all(entry["retryable"] for entry in refused)
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(DiaryEntry)) is None


async def test_a_subagent_that_runs_too_long_is_stopped_by_the_clock(e2e_harness, monkeypatch):
    """AG-BUDGET-012 — tests/brd/tg_agent_shell/agents.feature"""
    monkeypatch.setattr("tg_agent_shell.session.SUBAGENT_DEADLINE_SECONDS", 0.05)

    async def never_returns_in_time(_call):
        await asyncio.sleep(1.0)
        return {"status": "ok"}

    slow = RoutedSubagent(
        name="diary",
        prompt=routed_prompt(DIARY_AGENT),
        read_tools=(
            ReadToolSpec(
                schema={
                    "type": "function",
                    "function": {
                        "name": "read_day",
                        "description": "Read the day.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                run=never_returns_in_time,
            ),
        ),
        mutation_tools=("diary",),
    )
    advisor, provider = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), turn(("read_day", {}), prefix="diary"), "Готово."],
        subagents=(slow,),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert route_receipts(provider)[0]["error"].startswith("diary did not finish within")
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
    assert [(run.kind, run.status, run.error_code) for run in runs] == [
        ("advisor", "completed", None),
        ("diary", "failed", "timeout"),
    ]


async def test_route_is_not_offered_without_a_roster(e2e_harness):
    advisor, provider = e2e_harness.advisor(["Готово."], subagents=())

    await advisor.handle("Привет")

    offered = {tool["function"]["name"] for tool in provider.options[0]["tools"]}
    assert "query_data" in offered
    assert "route" not in offered
    # The Diary belongs to its subagent, so the Advisor cannot write a day either.
    assert "diary" not in offered
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(DiaryEntry)) is None


async def test_two_domains_in_one_request_are_both_finished(e2e_harness):
    """AG-ROUTE-003 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, kind=CardKind.ACTION, title="Приготовить еду", effort_points=2
        )
        await session.commit()
        card_id = card.id

    workspace = e2e_harness.workspace()
    diary = diary_subagent(e2e_harness)
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "workspace_mutator"})),
            turn(("card", {"mode": "update", "id": card_id, "title": "Приготовить пиццу"})),
        ],
        subagents=(workspace, diary),
    )
    first = await advisor.handle(
        "Переименуй действие в Приготовить пиццу и запиши вчерашний день в дневник"
    )
    assert first.kind is AIOutcomeKind.PROPOSAL

    # Saving resumes the workspace, whose receipt resumes the Advisor, which routes on.
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()
    provider.responses.extend(
        [
            "Карточка переименована.",
            turn(("route", {"name": "diary"})),
            turn(("read_day", {}), prefix="diary"),
            turn(
                ("diary", {"mode": "update", "date": TODAY, "pov": "Готовил пиццу."}),
                prefix="diary",
            ),
        ]
    )
    second = await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    # The Diary half is reached, and it is a screen of its own.
    assert second.kind is AIOutcomeKind.PROPOSAL
    assert second.proposal_id != first.proposal_id
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
    assert [(run.kind, run.status, run.parent_run_id) for run in runs] == [
        ("advisor", "awaiting_approval", None),
        ("workspace_mutator", "completed", 1),
        ("diary", "awaiting_approval", 1),
    ]
    # The Advisor routed on because it read what the workspace had already saved.
    receipt = route_receipts(provider)[0]
    assert receipt["subagent"] == "workspace_mutator"
    assert receipt["did"] == [
        "✅ Saved — Edit Action “Приготовить пиццу” "
        "(Title: Приготовить еду → Приготовить пиццу)"
    ]


async def test_a_failed_subagent_comes_back_as_an_error_the_advisor_reports(e2e_harness):
    """AG-RECEIPT-007 — tests/brd/tg_agent_shell/agents.feature"""
    class ExplodingReader:
        async def day_transcript(self, *_args, **_kwargs):
            raise RuntimeError("history is unreachable")

    diary = RoutedSubagent(
        name="diary",
        prompt=routed_prompt(DIARY_AGENT),
        read_tools=(day_read_tool(ExplodingReader(), chat_id=42),),
        mutation_tools=("diary",),
    )
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_day", {}), prefix="diary"),
            "Не смог прочитать день.",
        ],
        subagents=(diary,),
    )

    outcome = await advisor.handle("Запиши сегодняшний день")

    assert outcome.message == "Не смог прочитать день."
    receipt = route_receipts(provider)[0]
    assert receipt["outcome"] == "error"
    assert receipt["error"]
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
    assert [(run.kind, run.status) for run in runs] == [
        ("advisor", "completed"),
        ("diary", "failed"),
    ]


async def test_the_second_subagent_reads_what_the_first_one_saved(e2e_harness):
    """AG-ROUTE-003 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, kind=CardKind.ACTION, title="Приготовить еду", effort_points=2
        )
        await session.commit()
        card_id = card.id

    workspace = e2e_harness.workspace()
    diary = diary_subagent(e2e_harness)
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "workspace_mutator"})),
            turn(("card", {"mode": "update", "id": card_id, "title": "Приготовить пиццу"})),
        ],
        subagents=(workspace, diary),
    )
    first = await advisor.handle("Переименуй действие и запиши день")
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()
    provider.responses.extend(
        [
            "Переименовал.",
            turn(("route", {"name": "diary"})),
            turn(("diary", {"mode": "update", "date": TODAY, "pov": "Готовил пиццу."})),
        ]
    )

    await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    # The Diary's own context names what the workspace already saved, in the owner's words.
    diary_seen = next(item for item in provider.calls[-1] if item["role"] == "user")
    context = str(diary_seen["content"])
    assert "✅ Saved — Edit Action “Приготовить пиццу”" in context
    # It sits outside the conversation, so the subagent window cannot trim it.
    conversation = context[context.index("<Conversation>") :]
    assert "<User>Переименуй действие и запиши день</User>" in conversation
    assert context.index("[System]: Already saved") > context.index(conversation)


DIARY_DRAFT = {
    "mode": "update",
    "date": TODAY,
    "pov": "Закрыл рынок, хоть и поздно.",
    "remark": "One thing finished is still a finished day.",
    "feeling_score": 6,
}


async def _interrupted_diary(e2e_harness, *, then: list):
    """Run to a Diary screen, then have the owner write over it instead of deciding.

    Returns the advisor, the provider, and the text the frozen screen is rewritten with.
    """
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_day", {}), prefix="diary"),
            turn(("diary", DIARY_DRAFT), prefix="diary"),
            *then,
        ],
        subagents=(diary_subagent(e2e_harness),),
    )
    proposal = await advisor.handle("Запиши, как прошёл день")
    assert proposal.kind is AIOutcomeKind.PROPOSAL
    assert proposal.proposal_id is not None
    advisor.reviews.end_proposal(proposal.proposal_id)
    frozen = await advisor.cancel_approval_for_proposal(proposal.proposal_id)
    return advisor, provider, frozen


async def test_words_over_a_screen_continue_the_request_that_opened_it(e2e_harness):
    """AG-WORDS-016 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider, frozen = await _interrupted_diary(
        e2e_harness, then=["Понял, перепишу короче."]
    )
    async with e2e_harness.sessions() as session:
        before = list(await session.scalars(select(AgentRun.id).order_by(AgentRun.id)))

    outcome = await advisor.handle("то же, но короче")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == "Понял, перепишу короче."
    assert "🗑 Discarded" in frozen
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
    # No second request was opened: the same Advisor run answered the words.
    assert [run.id for run in runs] == before
    assert [(run.kind, run.status) for run in runs] == [
        ("advisor", "completed"),
        ("diary", "abandoned"),
    ]


async def test_the_resumed_request_is_told_what_was_proposed_and_what_was_refused(e2e_harness):
    """AG-WORDS-017 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider, _frozen = await _interrupted_diary(
        e2e_harness, then=["Понял, перепишу короче."]
    )

    await advisor.handle("то же, но короче")

    receipt = route_receipts(provider)[0]
    assert receipt["subagent"] == "diary"
    assert receipt["outcome"] == "error"
    # What became of every change, and that the owner answered with words instead.
    assert any("Discarded" in line for line in receipt["did"])
    assert "wrote to you instead" in receipt["error"]
    # Those words are the newest thing the resumed request reads.
    assert "то же, но короче" in json.dumps(provider.calls[3], ensure_ascii=False)


async def test_a_correction_reaches_the_session_that_wrote_the_refused_proposal(e2e_harness):
    """AG-WORDS-018 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider, _frozen = await _interrupted_diary(
        e2e_harness,
        then=[
            turn(("route", {"name": "diary"}), prefix="again"),
            turn(("diary", {**DIARY_DRAFT, "pov": "Закрыл рынок."}), prefix="fix"),
        ],
    )
    async with e2e_harness.sessions() as session:
        diary_run_id = await session.scalar(
            select(AgentRun.id).where(AgentRun.kind == "diary")
        )

    outcome = await advisor.handle("то же, но короче")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
    # The very same session, not a fresh one that would rewrite the day from scratch.
    assert [(run.id, run.kind) for run in runs] == [(1, "advisor"), (diary_run_id, "diary")]
    # It resumed holding the draft it had already written.
    drafted = [
        json.loads(call["function"]["arguments"])["pov"]
        for message in provider.calls[4]
        for call in message.get("tool_calls") or []
        if call["function"]["name"] == "diary"
    ]
    assert drafted == ["Закрыл рынок, хоть и поздно."]


async def test_the_interrupted_session_reads_that_the_owner_wrote_instead(e2e_harness):
    """AG-WORDS-019 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider, _frozen = await _interrupted_diary(
        e2e_harness,
        then=[
            turn(("route", {"name": "diary"}), prefix="again"),
            turn(("diary", {**DIARY_DRAFT, "pov": "Закрыл рынок."}), prefix="fix"),
        ],
    )

    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).where(AgentRun.kind == "diary"))
    notice = json.dumps(run.state_json["transcript"], ensure_ascii=False)
    assert "did not decide this" in notice
    assert "wrote to you instead" in notice
    assert "Never propose the refused change again" in notice

    await advisor.handle("то же, но короче")

    # It read that notice on the way back in, so it corrects instead of repeating itself.
    assert "wrote to you instead" in json.dumps(provider.calls[4], ensure_ascii=False)


async def test_unfinished_work_ends_with_the_request_that_started_it(e2e_harness):
    """AG-WORDS-020 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, _provider, _frozen = await _interrupted_diary(
        e2e_harness, then=["Хорошо, забудем про день."]
    )
    async with e2e_harness.sessions() as session:
        assert (
            await session.scalar(select(AgentRun.status).where(AgentRun.kind == "diary"))
        ) == "interrupted"

    await advisor.handle("забудь, лучше расскажи про спринт")

    async with e2e_harness.sessions() as session:
        assert (
            await session.scalar(select(AgentRun.status).where(AgentRun.kind == "diary"))
        ) == "abandoned"


async def test_saving_finishes_the_subagent_and_the_next_route_starts_fresh(e2e_harness):
    """AG-WORDS-021 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("diary", DIARY_DRAFT), prefix="diary"),
            "Записал день.",
            turn(("route", {"name": "diary"}), prefix="again"),
            turn(("diary", {**DIARY_DRAFT, "pov": "И ещё одно."}), prefix="second"),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )
    proposal = await advisor.handle("Запиши день")
    assert proposal.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(
            session, advisor.reviews, PROPOSALS, proposal.proposal_id
        )
        await session.commit()

    outcome = await advisor.resolve_approval(
        proposal.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    assert outcome is not None and outcome.kind is AIOutcomeKind.PROPOSAL
    async with e2e_harness.sessions() as session:
        diary_runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.kind == "diary").order_by(AgentRun.id)
            )
        )
    # Save finished the first one, so routing back inside the same request opened a second.
    assert [run.status for run in diary_runs] == ["completed", "awaiting_approval"]
    assert len(diary_runs) == 2


async def test_every_session_reads_through_the_one_door_and_no_one_declares_it_twice(
    e2e_harness,
) -> None:
    """`query_data` comes from the adapters, so the Advisor and a subagent share one door."""
    advisor, _ = e2e_harness.advisor(
        ["Готово."], subagents=(e2e_harness.workspace(), diary_subagent(e2e_harness))
    )

    for kind in ("advisor", "workspace_mutator", "diary"):
        definition = advisor.adapters.definition(kind)
        names = [tool["function"]["name"] for tool in definition.tools]
        assert names.count("query_data") == 1, kind
        # A session's own read tools are its own: none of them is a name the adapters answer.
        assert not set(definition.read_specs) & IMMEDIATE_TOOLS, kind
