from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import select

from safwa.ai.context import DialogueMessage
from safwa.ai.diary import DIARY_PROMPT, day_read_tool, diary_clock
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.ai.service import query_read_tool
from safwa.ai.sql import ReadOnlyQueryRunner
from safwa.ai.subagents import RoutedSubagent
from safwa.constants import DIARY_HISTORY_MESSAGES
from safwa.domain import create_card, finish_action
from safwa.enums import CardKind, CardStage
from safwa.models import AgentRun, AgentStep, DiaryEntry

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
                id=f"{prefix}-{index}", name=name, arguments=json.dumps(arguments)
            )
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


def diary_subagent(
    harness, transcript: str = "[08:40] [User]: Долгий день, но рынок закрыл."
) -> RoutedSubagent:
    """The real Diary subagent: the same session shape the Advisor runs on."""
    return RoutedSubagent(
        name="diary",
        purpose="the Diary",
        instructions=DIARY_PROMPT,
        read_tools=(
            day_read_tool(
                StubDayReader(transcript), chat_id=42, timezone="Europe/Istanbul"
            ),
            query_read_tool(ReadOnlyQueryRunner(harness.database_path)),
        ),
        mutation_tools=("diary",),
        history_messages=DIARY_HISTORY_MESSAGES,
        clock=lambda: diary_clock("Europe/Istanbul"),
    )


async def test_a_routed_subagent_answers_the_owner_in_its_own_words(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_day", {}), prefix="diary"),
            "Тот день у тебя записан: [08.03.2026](diary:4).",
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Что там в дневнике за восьмое?")

    # Nothing is relayed: the subagent's own sentence is the answer, citations and all.
    assert outcome.kind == "answer"
    assert outcome.message == "Тот день у тебя записан: [08.03.2026](diary:4)."
    # It reads under its own prompt, not the Advisor's.
    assert str(provider.calls[1][0]["content"]).startswith("# Safwa")
    assert "You keep the owner's Diary" in str(provider.calls[1][0]["content"])
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
        kinds = [
            step.kind for step in await session.scalars(select(AgentStep).order_by(AgentStep.id))
        ]
    assert [(run.kind, run.status) for run in runs] == [
        ("advisor", "completed"),
        ("diary", "completed"),
    ]
    assert kinds == ["route", "read"]


async def test_a_routed_subagent_proposes_for_itself(e2e_harness):
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
                ("query_safwa", {"sql": "SELECT card_id, operation FROM ai_card_events"}),
                prefix="diary",
            ),
            turn(
                (
                    "diary",
                    {
                        "mode": "update",
                        "date": TODAY,
                        "pov": "Закрыл рынок, хоть и поздно.",
                        "ai_comment": "One thing finished is still a finished day.",
                        "feeling_score": 6,
                    },
                ),
                prefix="diary",
            ),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.kind == "proposal"
    # The Advisor's own context never carried the day: it handed the turn over first.
    assert "Закрыл рынок" not in json.dumps(provider.calls[0], ensure_ascii=False)
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
        batch = await session.scalar(
            select(AgentStep).where(AgentStep.kind == "approval_batch")
        )
    # The Diary session waits for the screen; the Advisor's is long finished.
    assert [(run.kind, run.status) for run in runs] == [
        ("advisor", "completed"),
        ("diary", "awaiting_approval"),
    ]
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
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.message == "Записал."
    rejected = json.loads(
        next(item for item in provider.calls[1] if item.get("role") == "tool")["content"]
    )
    assert rejected["code"] == "unknown_subagent"
    assert rejected["retryable"] is True


async def test_a_routed_subagent_is_offered_only_its_own_tools(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), "Готово."],
        subagents=(diary_subagent(e2e_harness),),
    )

    await advisor.handle("Что там в дневнике?")

    offered = {tool["function"]["name"] for tool in provider.options[1]["tools"]}
    assert offered == {"read_day", "query_safwa", "diary"}
    # No recursion, and no reach into the board.
    assert "route" not in offered
    assert "card" not in offered


async def test_the_board_owns_every_mutation_tool(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "board"})),
            turn(("card", {"mode": "create", "kind": "goal", "title": "Быть здоровым"})),
        ]
    )

    outcome = await advisor.handle("Сделай цель Быть здоровым")

    assert outcome.kind == "proposal"
    # The Advisor has no way to describe a change instead of routing it: it has no tool.
    advisor_tools = {tool["function"]["name"] for tool in provider.options[0]["tools"]}
    assert advisor_tools == {"query_safwa", "route"}
    board_tools = {tool["function"]["name"] for tool in provider.options[1]["tools"]}
    assert board_tools == {
        "query_safwa",
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
        ("advisor", "completed"),
        ("board", "awaiting_approval"),
    ]


async def test_each_subagent_sees_only_the_conversation_it_declared(e2e_harness):
    dialogue = [
        DialogueMessage(role="user", content=f"[User]: сообщение {index}") for index in range(6)
    ]
    board = e2e_harness.board()
    diary = diary_subagent(e2e_harness)
    advisor, provider = e2e_harness.advisor(
        [
            turn(("read_day", {}), prefix="diary"),
            "Записал.",
            turn(("tag", {"mode": "create", "name": "VrWalk"})),
        ],
        subagents=(board, diary),
    )

    await advisor.handle("Что в дневнике?", dialogue=dialogue)
    await advisor.handle("Заведи тег VrWalk", dialogue=dialogue)

    # The Diary reads the day itself, so it needs only the tail — enough to be told what to
    # change about what it just proposed.
    diary_seen = [item for item in provider.calls[0] if item.get("role") == "user"]
    assert [str(item["content"]) for item in diary_seen[:-1]] == [
        f"[User]: сообщение {index}"
        for index in range(6 - DIARY_HISTORY_MESSAGES, 6)
    ]
    assert diary.history_messages == DIARY_HISTORY_MESSAGES
    # The board reasons about the plan, so it gets the whole window plus the board state.
    board_seen = [str(item["content"]) for item in provider.calls[2] if item.get("role") == "user"]
    assert board_seen[0].startswith("[System]: Current planning state:")
    assert board_seen[1:] == [f"[User]: сообщение {index}" for index in range(6)]
    assert board.history_messages is None


async def test_route_is_not_offered_without_a_roster(e2e_harness):
    advisor, provider = e2e_harness.advisor(["Готово."], subagents=())

    await advisor.handle("Привет")

    offered = {tool["function"]["name"] for tool in provider.options[0]["tools"]}
    assert "query_safwa" in offered
    assert "route" not in offered
    # The Diary belongs to its subagent, so the Advisor cannot write a day either.
    assert "diary" not in offered
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(DiaryEntry)) is None
