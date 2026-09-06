"""What the engine does around a tool call: who may answer for it, and what it adds.

The adapters are built with nothing but a trail and an empty screen catalogue here: none
of these calls reaches a database, so the roster the rest of `ToolAdapters` needs is not
built either.
"""

from __future__ import annotations

from typing import Any

import pytest

from llm_gateway import ToolCall
from tg_agent_shell.ai.contracts import ToolResultStatus
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.ai.tools import (
    AgentSession,
    HelperPort,
    ToolAdapters,
    WatcherFailed,
)
from tg_agent_shell.foundation.screens import ScreenCatalogue

READ = ToolCall(id="1", name="read_thing", arguments_json="{}")


class _Trail:
    """The one record of what a session did. Nothing here reads it back."""

    async def step(self, *arguments: Any, **keywords: Any) -> None:
        return None


def _session(ran: list[str]) -> AgentSession:
    async def run(call: ToolCall) -> dict[str, Any]:
        ran.append(call.name)
        return {"rows": "what the tool found"}

    spec = ReadToolSpec(
        schema={"type": "function", "function": {"name": "read_thing"}}, run=run
    )
    return AgentSession(run_id=1, tools=(), read_specs={"read_thing": spec})


def _adapters(**watchers: Any) -> ToolAdapters:
    return ToolAdapters(None, None, None, _Trail(), ScreenCatalogue.of(()), **watchers)


async def test_ag_tool_031_a_watcher_that_answers_refuses_the_call() -> None:
    """AG-TOOL-031 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []
    refusal = {"status": "error", "code": "not_this_one"}

    async def refuse(agent: AgentSession, call: ToolCall) -> dict[str, Any]:
        return refusal

    outcome = await _adapters(before_tool=(refuse,)).run(_session(ran), READ)

    assert outcome.result is refusal
    assert ran == []


async def test_ag_tool_031_a_watcher_that_answers_with_nothing_lets_the_call_run() -> None:
    """AG-TOOL-031 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def watch(agent: AgentSession, call: ToolCall) -> None:
        return None

    outcome = await _adapters(before_tool=(watch,)).run(_session(ran), READ)

    assert ran == ["read_thing"]
    assert outcome.result == {"rows": "what the tool found"}


async def test_ag_tool_031_a_watcher_that_fails_ends_the_turn_and_is_named() -> None:
    """AG-TOOL-031 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def broken(agent: AgentSession, call: ToolCall) -> None:
        raise RuntimeError("the watcher never decided")

    with pytest.raises(WatcherFailed) as failure:
        await _adapters(before_tool=(broken,)).run(_session(ran), READ)

    assert ran == []
    said = str(failure.value)
    assert "broken" in said and "before" in said and "read_thing" in said
    assert "the watcher never decided" in said


async def test_ag_tool_032_a_watcher_that_fails_after_the_call_is_named_too() -> None:
    """AG-TOOL-032 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def broken(agent: AgentSession, call: ToolCall, result: Any) -> None:
        raise RuntimeError("nothing was added")

    with pytest.raises(WatcherFailed) as failure:
        await _adapters(after_tool=(broken,)).run(_session(ran), READ)

    # The call itself ran: only what watched it fell over.
    assert ran == ["read_thing"]
    said = str(failure.value)
    assert "broken" in said and "after" in said and "read_thing" in said


async def test_ag_tool_032_a_watcher_reads_the_call_and_adds_to_its_result() -> None:
    """AG-TOOL-032 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []
    seen: list[str] = []

    async def watch(agent: AgentSession, call: ToolCall, result: Any) -> None:
        seen.append(call.name)
        result["notice"] = "and one more thing to read"

    outcome = await _adapters(after_tool=(watch,)).run(_session(ran), READ)

    assert seen == ["read_thing"]
    assert outcome.result["notice"] == "and one more thing to read"


def test_ag_tool_033_a_read_that_failed_earns_no_offer() -> None:
    """AG-TOOL-033 — tests/brd/tg_agent_shell/agents.feature"""
    # The gate is asked directly: a scripted read would prove the same thing behind a database.
    adapters = _adapters(
        helpers={
            "any": HelperPort(
                run=None,  # type: ignore[arg-type]
                offer_when=lambda sql, rows: True,
                offer="call the helper",
            )
        }
    )
    session = AgentSession(run_id=1, tools=())
    sql = "SELECT nope FROM ai_cards"

    failed = [{"status": ToolResultStatus.ERROR.value, "error": "no such column: nope"}]
    assert adapters._offered_helper(session, sql, failed) is None
    assert adapters._offered_helper(session, sql, [{"n": 1}]) == "call the helper"
