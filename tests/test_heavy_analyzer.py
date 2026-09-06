from __future__ import annotations

import json
from typing import Any

import pytest

from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.bootstrap.modules import AGENTS, HELPERS, SYSTEM_PROMPT
from safwa.features.heavy_analyzer import agent as heavy_analyzer
from safwa.features.heavy_analyzer.agent import worth_a_helper
from tg_agent_shell.ai.tools import IMMEDIATE_TOOLS, ROOT_SESSION_TOOLS

HEAVY_ANALYZER_PROMPT = HELPERS[heavy_analyzer.NAME].instructions


class ScriptedMini:
    """A provider that answers a mini session from a list of prepared turns."""

    def __init__(self, turns: list[CompletionTurn]) -> None:
        self.turns = list(turns)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("The helper made an unexpected provider call")
        return self.turns.pop(0)


def query(sql: str, call_id: str = "q1") -> CompletionTurn:
    return CompletionTurn(
        content="",
        tool_calls=(
            ToolCall(id=call_id, name="query_data", arguments_json=json.dumps({"sql": sql})),
        ),
    )


def terminal(name: str, arguments: dict[str, Any] | None = None) -> CompletionTurn:
    return CompletionTurn(
        content="",
        tool_calls=(
            ToolCall(id=f"t-{name}", name=name, arguments_json=json.dumps(arguments or {})),
        ),
    )


# ---------------------------------------------------------------- what it may read


def test_han_read_012_the_event_log_belongs_to_the_helper(read_views) -> None:
    """HAN-READ-012 — tests/brd/heavy_analyzer.feature"""
    workspace = next(agent.instructions for agent in AGENTS if agent.name == "workspace_mutator")

    assert "ai_card_events" in HEAVY_ANALYZER_PROMPT
    assert "ai_card_events" not in SYSTEM_PROMPT
    assert "ai_card_events" not in workspace
    # What the log is for: which of two hands made the change.
    assert "user_ui | ai" in HEAVY_ANALYZER_PROMPT


def test_a_reader_is_scoped_by_the_list_it_is_given() -> None:
    """AG-READ-027 — tests/brd/tg_agent_shell/agents.feature"""
    # The Diary is the Advisor's to read and the workspace's to leave alone.
    workspace = next(agent.instructions for agent in AGENTS if agent.name == "workspace_mutator")

    assert "ai_diary" in SYSTEM_PROMPT
    assert "ai_diary" not in workspace
    assert "{views}" not in workspace and "{views}" not in SYSTEM_PROMPT


# ------------------------------------------------------------------ what triggers it


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, title FROM ai_cards WHERE stage = 'today'",
        "SELECT count(*) FROM ai_cards WHERE stage = 'today'",
        "SELECT sum(effort_points) FROM ai_cards WHERE kind = 'action'",
    ],
)
def test_han_offer_002_a_flat_read_earns_nothing(sql: str) -> None:
    """HAN-OFFER-002 — tests/brd/heavy_analyzer.feature"""
    assert not worth_a_helper(sql, [{"n": 1}])


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a.id FROM ai_cards a JOIN ai_checks k ON k.card_id = a.id",
        "SELECT status, count(*) FROM ai_checks GROUP BY status",
        "WITH recent AS (SELECT id FROM ai_cards) SELECT * FROM recent",
        "SELECT id FROM ai_cards WHERE id IN (SELECT card_id FROM ai_checks)",
        "SELECT id, row_number() OVER (ORDER BY id) FROM ai_cards",
    ],
)
def test_han_offer_001_a_read_past_one_flat_scan_earns_the_helper(sql: str) -> None:
    """HAN-OFFER-001 — tests/brd/heavy_analyzer.feature"""
    assert worth_a_helper(sql, [{"n": 1}])


def test_han_offer_003_a_result_the_row_limit_cut_earns_the_helper() -> None:
    """HAN-OFFER-003 — tests/brd/heavy_analyzer.feature"""
    flat = "SELECT id FROM ai_cards"
    assert not worth_a_helper(flat, [{"id": 1}])
    assert worth_a_helper(flat, [{"id": 1}, {"notice": "50 rows shown; more matched."}])


# ------------------------------------------------------------------ what it hands back


async def test_han_ask_007_the_helper_forwards_its_last_read(read_views) -> None:
    """HAN-ASK-007 — tests/brd/heavy_analyzer.feature"""
    _, runner = read_views
    sql = "SELECT count(*) AS n FROM ai_cards"
    provider = ScriptedMini(
        [
            CompletionTurn(
                content="I will look at the cards now.",
                tool_calls=query(sql).tool_calls,
            ),
            terminal("forward_output"),
        ]
    )

    result = await heavy_analyzer.analyse(
        provider,
        runner,
        prompt="stub",
        conversation="<Conversation></Conversation>",
        request="How many Cards are there?",
    )

    assert result["rows"] == [{"n": 0}]
    assert result["sql"] == sql
    # Its own words were written and went nowhere: only the read reaches the caller.
    assert "I will look at the cards now." not in json.dumps(result, ensure_ascii=False)


async def test_a_broken_read_never_becomes_the_answer(read_views) -> None:
    """A rejected SELECT is the helper's to repair, not a result to forward."""
    _, runner = read_views
    provider = ScriptedMini(
        [
            query("SELECT id FROM ai_cards", call_id="good"),
            query("SELECT id FROM secrets", call_id="bad"),
            terminal("forward_output"),
        ]
    )

    result = await heavy_analyzer.analyse(
        provider, runner, prompt="stub", conversation="", request="anything"
    )

    assert result["sql"] == "SELECT id FROM ai_cards"
    assert "status" not in result


async def test_han_ask_011_a_helper_that_gets_nowhere_says_so(read_views) -> None:
    """HAN-ASK-011 — tests/brd/heavy_analyzer.feature"""
    _, runner = read_views
    provider = ScriptedMini(
        [terminal("report_failure", {"explanation": "The Diary holds no weigh-ins."})]
    )

    result = await heavy_analyzer.analyse(
        provider, runner, prompt="stub", conversation="", request="Weigh-ins?"
    )

    assert result["status"] == "error"
    assert result["error"] == "The Diary holds no weigh-ins."
    assert "hint" in result


async def test_a_budget_spent_without_an_answer_is_a_failure(read_views) -> None:
    """AG-HELPER-028 — tests/brd/tg_agent_shell/agents.feature"""
    # The budget bounds the session (HEAVY_ANALYZER_MAX_TOOL_CALLS = 10), not the first error.
    _, runner = read_views
    reads = heavy_analyzer.HEAVY_ANALYZER_MAX_TOOL_CALLS + 1
    provider = ScriptedMini([query("SELECT id FROM ai_cards", f"q{n}") for n in range(reads)])

    result = await heavy_analyzer.analyse(
        provider, runner, prompt="stub", conversation="", request="anything"
    )

    assert result["status"] == "error"
    assert "budget" in result["error"]
    assert not provider.turns


async def test_forwarding_before_reading_anything_is_refused(read_views) -> None:
    """AG-HELPER-028 — tests/brd/tg_agent_shell/agents.feature"""
    # `forward_output` forwards a read; there has to be one.
    _, runner = read_views
    provider = ScriptedMini([terminal("forward_output")])

    result = await heavy_analyzer.analyse(
        provider, runner, prompt="stub", conversation="", request="anything"
    )

    assert result["status"] == "error"


# --------------------------------------------------------------- what it is not allowed


async def test_han_ask_006_the_request_follows_the_conversation(read_views) -> None:
    """HAN-ASK-006 — tests/brd/heavy_analyzer.feature"""
    _, runner = read_views
    provider = ScriptedMini(
        [query("SELECT id FROM ai_cards"), terminal("forward_output")]
    )

    await heavy_analyzer.analyse(
        provider,
        runner,
        prompt="stub",
        conversation="<Conversation><User>How was my week?</User></Conversation>",
        request="Count done Actions in the last 7 days.",
    )

    context = str(provider.requests[0].messages[1]["content"])
    assert context.endswith(
        "</Conversation>\n<Request from AI>Count done Actions in the last 7 days."
        "</Request from AI>"
    )


async def test_han_ask_009_the_helper_holds_no_tool_that_changes_anything(read_views) -> None:
    """HAN-ASK-009 — tests/brd/heavy_analyzer.feature"""
    _, runner = read_views
    provider = ScriptedMini([terminal("report_failure", {"explanation": "no"})])

    await heavy_analyzer.analyse(
        provider, runner, prompt="stub", conversation="", request="Cancel everything."
    )

    offered = {tool["function"]["name"] for tool in provider.requests[0].tools}
    assert offered == {"query_data", "forward_output", "report_failure"}


def test_han_ask_010_no_routing_rule_names_a_helper() -> None:
    """HAN-ASK-010 — tests/brd/heavy_analyzer.feature"""
    rules = SYSTEM_PROMPT.split("# Routing", 1)[1].split("\n# ", 1)[0]

    assert heavy_analyzer.NAME not in rules
    assert heavy_analyzer.NAME not in SYSTEM_PROMPT
    # And the tool is not something the Advisor carries into a turn either.
    assert "call_helper" not in {tool["function"]["name"] for tool in ROOT_SESSION_TOOLS}
    assert "call_helper" not in SYSTEM_PROMPT


def test_call_helper_runs_inside_the_turn_and_never_becomes_a_proposal() -> None:
    """AG-HELPER-025 — tests/brd/tg_agent_shell/agents.feature"""
    assert "call_helper" in IMMEDIATE_TOOLS
