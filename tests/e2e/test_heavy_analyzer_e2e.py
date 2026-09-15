from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import HOOKS, MODULES, PROPOSALS, REGISTRY
from safwa.features.cards.use_cases import create_card
from telegram_llm import DialogueMessage
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.hooks.contracts import HookSwitch
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.proposals.use_cases import approve_proposal
from tg_agent_shell.registry import Registry

QUESTION = "Как у меня с подтягиваниями?"


def read(sql: str, call_id: str = "q1") -> CompletionTurn:
    return CompletionTurn(
        content="",
        tool_calls=(
            ToolCall(id=call_id, name="query_data", arguments_json=json.dumps({"sql": sql})),
        ),
    )


def ask_helper(request: str, name: str = "heavy_analyzer") -> CompletionTurn:
    return CompletionTurn(
        content="",
        tool_calls=(
            ToolCall(
                id="h1",
                name="call_helper",
                arguments_json=json.dumps({"name": name, "request": request}),
            ),
        ),
    )


def recording_helper(seen: list[dict[str, str]], rows: list[dict[str, Any]] | None = None):
    async def helper(*, conversation: str, request: str) -> dict[str, Any]:
        seen.append({"conversation": conversation, "request": request})
        return {"helper": "heavy_analyzer", "sql": "SELECT 1", "rows": rows or [{"n": 2}]}

    return helper


def tools_of(provider, index: int) -> set[str]:
    return {tool["function"]["name"] for tool in provider.options[index]["tools"]}


async def test_han_offer_001_a_complex_read_offers_the_helper(e2e_harness) -> None:
    """HAN-OFFER-001 — tests/brd/heavy_analyzer.feature"""
    seen: list[dict[str, str]] = []
    advisor, provider = e2e_harness.advisor(
        [
            read("SELECT status, count(*) FROM ai_checks GROUP BY status"),
            "Всё посчитано.",
        ],
        helpers={"heavy_analyzer": recording_helper(seen)},
    )

    await advisor.handle(QUESTION)

    result = json.loads(provider.calls[1][-1]["content"])
    assert "call_helper" in result[-1]["notice"]
    assert "heavy_analyzer" in result[-1]["notice"]
    # The first turn was not offered the tool; the read is what earned it.
    assert "call_helper" not in tools_of(provider, 0)
    assert "call_helper" in tools_of(provider, 1)


async def test_a_data_column_named_status_does_not_turn_a_read_into_an_error(e2e_harness):
    """HAN-OFFER-001 — tests/brd/heavy_analyzer.feature"""
    advisor, provider = e2e_harness.advisor(
        [read("WITH sample AS (SELECT 'error' AS status) SELECT * FROM sample"), "Read."],
        helpers={"heavy_analyzer": recording_helper([])},
    )
    await advisor.handle(QUESTION)
    result = json.loads(provider.calls[1][-1]["content"])
    assert result[0] == {"status": "error"}
    assert "call_helper" in result[-1]["notice"]
    assert "call_helper" in tools_of(provider, 1)


async def test_switching_the_offer_off_keeps_the_helper_operation(e2e_harness):
    """AG-HOOK-035 — tests/brd/tg_agent_shell/agents.feature"""
    seen = []
    advisor, provider = e2e_harness.advisor(
        [read("SELECT status, count(*) FROM ai_checks GROUP BY status"), "Read."],
        helpers={"heavy_analyzer": recording_helper(seen)},
    )

    async def everything_off(session, name):
        return False

    # The real offer has no switch; give every hook one here so the policy can turn it off.
    advisor.adapters.hooks = Registry.of(
        MODULES, world=REGISTRY.proposals.world,
        hooks=tuple(replace(item, switch=HookSwitch("Offer", "Offers the helper.")) for item in HOOKS),
        hook_policy=everything_off,
    ).hooks
    await advisor.handle(QUESTION)
    assert "call_helper" not in tools_of(provider, 1)
    assert "call_helper" not in provider.calls[1][-1]["content"]
    result = await advisor.adapters.helpers["heavy_analyzer"].run(
        conversation="", request="Explicit analysis",
    )
    assert result["rows"] == [{"n": 2}]
    assert len(seen) == 1


async def test_han_offer_002_a_simple_read_offers_nothing(e2e_harness) -> None:
    """HAN-OFFER-002 — tests/brd/heavy_analyzer.feature"""
    advisor, provider = e2e_harness.advisor(
        [read("SELECT count(*) FROM ai_cards WHERE stage = 'today'"), "Ничего в Today."],
        helpers={"heavy_analyzer": recording_helper([])},
    )

    await advisor.handle(QUESTION)

    result = json.loads(provider.calls[1][-1]["content"])
    assert all("notice" not in row for row in result)
    assert "call_helper" not in tools_of(provider, 1)


async def test_han_offer_003_a_result_that_was_cut_offers_the_helper(e2e_harness) -> None:
    """HAN-OFFER-003 — tests/brd/heavy_analyzer.feature"""
    async with e2e_harness.sessions() as session:
        for title in ("Run", "Read", "Rest"):
            await create_card(session, title=title, kind="action", effort_points=1)
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [read("SELECT id, title FROM ai_cards"), "Слишком много."],
        helpers={"heavy_analyzer": recording_helper([])},
    )
    # One flat read, so nothing but the cap can offer the helper here.
    advisor.adapters.query_runner.row_limit = 1

    await advisor.handle(QUESTION)

    notice = json.loads(provider.calls[1][-1]["content"])[-1]["notice"]
    assert "row(s) are shown" in notice or "budget" in notice
    assert "call_helper" in notice
    assert "call_helper" in tools_of(provider, 1)


async def test_han_offer_004_a_read_that_failed_offers_nothing(e2e_harness) -> None:
    """HAN-OFFER-004 — tests/brd/heavy_analyzer.feature"""
    advisor, provider = e2e_harness.advisor(
        [read("SELECT id FROM ai_cards JOIN secrets ON 1=1"), "Не смог."],
        helpers={"heavy_analyzer": recording_helper([])},
    )

    await advisor.handle(QUESTION)

    result = json.loads(provider.calls[1][-1]["content"])
    assert result[0]["code"] == "unsafe_query"
    assert "call_helper" not in json.dumps(result)
    assert "call_helper" not in tools_of(provider, 1)


async def test_han_ask_006_the_helper_reads_the_conversation_and_the_question(
    e2e_harness,
) -> None:
    """HAN-ASK-006 — tests/brd/heavy_analyzer.feature"""
    seen: list[dict[str, str]] = []
    advisor, _ = e2e_harness.advisor(
        [
            read("SELECT status, count(*) FROM ai_checks GROUP BY status"),
            ask_helper("Сколько passed и missed по серии 7?"),
            "Готово.",
        ],
        helpers={"heavy_analyzer": recording_helper(seen)},
    )

    await advisor.handle(
        QUESTION, dialogue=[DialogueMessage(role="user", content=QUESTION)]
    )

    assert seen[0]["request"] == "Сколько passed и missed по серии 7?"
    assert seen[0]["conversation"].startswith("<Conversation>")
    assert QUESTION in seen[0]["conversation"]


async def test_han_ask_007_the_rows_come_back_and_the_advisor_answers(e2e_harness) -> None:
    """HAN-ASK-007 — tests/brd/heavy_analyzer.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            read("SELECT status, count(*) FROM ai_checks GROUP BY status"),
            ask_helper("Посчитай по всей серии."),
            "Подтянулся 12 раз, пропустил 3.",
        ],
        helpers={
            "heavy_analyzer": recording_helper(
                [], rows=[{"status": "passed", "n": 12}, {"status": "missed", "n": 3}]
            )
        },
    )

    outcome = await advisor.handle(QUESTION)

    handed_back = json.loads(provider.calls[2][-1]["content"])
    assert handed_back["rows"] == [{"status": "passed", "n": 12}, {"status": "missed", "n": 3}]
    assert handed_back["sql"] == "SELECT 1"
    # No screen: the turn ends in the Advisor's own words.
    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.proposal_id is None
    assert outcome.message == "Подтянулся 12 раз, пропустил 3."


async def test_han_ask_008_cancelling_the_turn_stops_the_helper(e2e_harness) -> None:
    """AG-HELPER-025 — tests/brd/tg_agent_shell/agents.feature"""
    entered = asyncio.Event()
    cancelled = False

    async def slow_helper(*, conversation: str, request: str) -> dict[str, Any]:
        nonlocal cancelled
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return {}

    advisor, _ = e2e_harness.advisor(
        [
            read("SELECT status, count(*) FROM ai_checks GROUP BY status"),
            ask_helper("Считай."),
            "Готово.",
        ],
        helpers={"heavy_analyzer": slow_helper},
    )

    turn = asyncio.create_task(advisor.handle(QUESTION))
    await asyncio.wait_for(entered.wait(), timeout=5)
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    assert cancelled


async def test_han_ask_010_a_subagent_cannot_be_called_as_a_helper(e2e_harness) -> None:
    """HAN-ASK-010 — tests/brd/heavy_analyzer.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            read("SELECT status, count(*) FROM ai_checks GROUP BY status"),
            ask_helper("Сделай что-нибудь.", name="workspace_mutator"),
            "Не вышло.",
        ],
        helpers={"heavy_analyzer": recording_helper([])},
    )

    await advisor.handle(QUESTION)

    refusal = json.loads(provider.calls[2][-1]["content"])
    assert refusal["code"] == "unknown_helper"
    assert "heavy_analyzer" in refusal["hint"]


async def test_han_ask_011_a_failed_helper_still_leaves_an_answer(e2e_harness) -> None:
    """HAN-ASK-011 — tests/brd/heavy_analyzer.feature"""

    async def broken_helper(*, conversation: str, request: str) -> dict[str, Any]:
        raise RuntimeError("the analyzer fell over")

    advisor, provider = e2e_harness.advisor(
        [
            read("SELECT status, count(*) FROM ai_checks GROUP BY status"),
            ask_helper("Считай."),
            "Не смог посчитать, но вот что вижу.",
        ],
        helpers={"heavy_analyzer": broken_helper},
    )

    outcome = await advisor.handle(QUESTION)

    result = json.loads(provider.calls[2][-1]["content"])
    assert result["status"] == "error"
    assert "fell over" in result["error"]
    assert outcome.message == "Не смог посчитать, но вот что вижу."


async def test_han_offer_005_the_offer_outlives_a_screen(e2e_harness) -> None:
    """AG-HELPER-026 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(session, title="Run", kind="action", effort_points=1)
        await session.commit()
        card_id = card.id

    advisor, provider = e2e_harness.advisor(
        [
            read("SELECT status, count(*) FROM ai_checks GROUP BY status"),
            CompletionTurn(
                content="",
                tool_calls=(
                    ToolCall(id="r1", name="route", arguments_json=json.dumps({"name": "workspace_mutator"})),
                ),
            ),
            CompletionTurn(
                content="Plan: one Action.",
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="card",
                        arguments_json=json.dumps(
                            {"mode": "update", "id": card_id, "title": "Бегать"}
                        ),
                    ),
                ),
            ),
        ],
        helpers={"heavy_analyzer": recording_helper([])},
    )
    first = await advisor.handle(QUESTION)
    assert first.kind is AIOutcomeKind.PROPOSAL

    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()
    provider.responses.extend(["Переименовал.", "Готово."])

    await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    # The screen belonged to `route`; the resumed Advisor keeps the tool it was shown.
    assert "call_helper" in tools_of(provider, -1)
