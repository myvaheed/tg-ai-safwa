"""What a feature sees of a tool call it did not declare.

The adapters are built with nothing but a trail here: none of these calls reaches a
database, so the roster the rest of `ToolAdapters` needs is not built either.
"""

from __future__ import annotations

from typing import Any

import pytest

from llm_gateway import ToolCall
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.ai.tools import AgentSession, ToolAdapters

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
    return ToolAdapters(None, None, None, _Trail(), None, **watchers)


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


async def test_ag_tool_031_a_watcher_that_fails_ends_the_turn() -> None:
    """AG-TOOL-031 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def broken(agent: AgentSession, call: ToolCall) -> None:
        raise RuntimeError("the watcher never decided")

    with pytest.raises(RuntimeError, match="never decided"):
        await _adapters(before_tool=(broken,)).run(_session(ran), READ)

    assert ran == []


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
