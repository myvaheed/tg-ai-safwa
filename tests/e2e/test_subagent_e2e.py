from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from safwa.ai.diary import DiarySubagent
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.ai.service import query_read_tool
from safwa.ai.sql import ReadOnlyQueryRunner
from safwa.domain import create_card, finish_action
from safwa.enums import CardKind, CardStage
from safwa.models import AgentRun, AgentStep, DiaryStamp

pytestmark = pytest.mark.e2e


class SubagentProvider:
    """The subagent's own boundary; the advisor keeps the harness one."""

    def __init__(self, turns: list[ProviderTurn]) -> None:
        self.turns = turns

    async def complete_turn(self, _messages, **_kwargs) -> ProviderTurn:
        if not self.turns:
            raise AssertionError("The subagent made an unexpected provider call")
        return self.turns.pop(0)


def turn(*calls: tuple[str, dict[str, object]], prefix: str = "call") -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(
                id=f"{prefix}-{index}", name=name, arguments=json.dumps(arguments)
            )
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


class StubDayReader:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    async def day_transcript(self, _chat_id: int, *, start, token_budget) -> str:  # noqa: ARG002
        return self.transcript


def diary_for(harness, responses: list[ProviderTurn]) -> tuple[DiarySubagent, SubagentProvider]:
    """The real Diary subagent over the real database, on its own scripted provider."""
    provider = SubagentProvider(list(responses))
    subagent = DiarySubagent(
        harness.sessions,
        provider,  # type: ignore[arg-type]
        StubDayReader("[08:40] [User]: Долгий день, но рынок закрыт.\n[08:41] Понимаю."),
        query_read_tool(ReadOnlyQueryRunner(harness.database_path)),
        chat_id=42,
        timezone="Europe/Istanbul",
    )
    return subagent, provider


async def test_a_subagent_report_reaches_the_model_without_the_entry_text(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, kind=CardKind.ACTION, title="Сходить на рынок", effort_points=2
        )
        await finish_action(session, card.id, CardStage.DONE)
        await session.commit()

    subagent, diary_provider = diary_for(
        e2e_harness,
        [
            turn(
                ("read_day", {}),
                (
                    "query_safwa",
                    {"sql": "SELECT card_id, operation FROM ai_card_events ORDER BY id"},
                ),
            ),
            turn(
                (
                    "diary_report",
                    {
                        "entry": "Закрыл рынок, хоть и поздно.",
                        "remark": "One thing finished is still a finished day.",
                    },
                )
            ),
        ],
    )
    advisor, provider = e2e_harness.advisor(
        [
            turn(("call_subagent", {"name": "diary", "request": "Write today's entry."})),
            "Твоя запись за сегодня готова.",
        ],
        subagents=(subagent,),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.kind == "answer"
    result = next(
        json.loads(item["content"])
        for item in provider.calls[1]
        if item.get("role") == "tool" and item.get("name") == "call_subagent"
    )
    assert result["shape"] == "draft"
    assert result["remark"].startswith("One thing finished")
    # The advisor is handed the stamp, never the body: it cannot edit what it never saw.
    assert "Закрыл рынок" not in json.dumps(provider.calls[1], ensure_ascii=False)
    async with e2e_harness.sessions() as session:
        stamp = await session.get(DiaryStamp, result["stamp"])
        assert stamp is not None
        assert stamp.body == "Закрыл рынок, хоть и поздно."
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
        kinds = [
            step.kind
            for step in await session.scalars(select(AgentStep).order_by(AgentStep.id))
        ]
    # The advisor's run and the subagent's are separate; the hand-off links them.
    assert [run.status for run in runs] == ["completed", "completed"]
    assert kinds == ["subagent_read", "subagent_read", "subagent_terminal", "subagent_call"]
    assert diary_provider.turns == []


async def test_a_subagent_and_a_mutation_in_one_response_rejects_the_mutation(e2e_harness):
    subagent, _ = diary_for(
        e2e_harness,
        [turn(("diary_report", {"entry": "Короткий день.", "remark": "Short."}))],
    )
    advisor, provider = e2e_harness.advisor(
        [
            turn(
                ("call_subagent", {"name": "diary", "request": "Write today's entry."}),
                ("card", {"mode": "create", "kind": "goal", "title": "Be healthy"}),
            ),
            turn(("card", {"mode": "create", "kind": "goal", "title": "Be healthy"})),
        ],
        subagents=(subagent,),
    )

    outcome = await advisor.handle("Запиши день и заведи цель")

    assert outcome.proposal_id is not None
    results = {
        item["name"]: json.loads(item["content"])
        for item in provider.calls[1]
        if item.get("role") == "tool"
    }
    assert results["call_subagent"]["shape"] == "draft"
    assert results["card"]["code"] == "mixed_read_and_mutation_tools"
    assert results["card"]["retryable"] is True


async def test_an_unknown_subagent_name_is_repaired_in_the_next_response(e2e_harness):
    subagent, _ = diary_for(
        e2e_harness,
        [turn(("diary_report", {"entry": "Короткий день.", "remark": "Short."}))],
    )
    advisor, provider = e2e_harness.advisor(
        [
            turn(("call_subagent", {"name": "journal", "request": "Write today's entry."})),
            turn(("call_subagent", {"name": "diary", "request": "Write today's entry."})),
            "Готово.",
        ],
        subagents=(subagent,),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.kind == "answer"
    rejected = json.loads(
        next(item for item in provider.calls[1] if item.get("role") == "tool")["content"]
    )
    assert rejected["code"] == "unknown_subagent"
    assert rejected["retryable"] is True


async def test_call_subagent_is_not_offered_when_no_runner_is_wired(e2e_harness):
    advisor, provider = e2e_harness.advisor(["Готово."])

    await advisor.handle("Привет")

    offered = {tool["function"]["name"] for tool in provider.options[0]["tools"]}
    assert "query_safwa" in offered
    assert "call_subagent" not in offered
