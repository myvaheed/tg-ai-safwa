"""A short session that must plan writes its plan before its call (AG-SESSION-064).

The session is the shell's, with a terminal of the test's own. Nothing here imports Safwa.
"""

from __future__ import annotations

import json

import pytest
from agent_turns import mutation_turn

from llm_gateway import ScriptedProvider
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import (
    MINI_SESSION_REPAIR_ROUNDS,
    PLAN_FIRST,
    MiniSessionError,
    TerminalTool,
    run_mini_session,
)


class Read(ToolInput):
    """The words were read."""


READ = TerminalTool("read", "The words were read.", Read)


async def _read(provider: ScriptedProvider, *, plan_required: bool = True) -> str:
    result = await run_mini_session(
        provider,
        system_prompt="Read the words.",
        context="Words: lunch",
        terminals=(READ,),
        max_tool_calls=1,
        plan_required=plan_required,
    )
    return result.name


async def test_ag_session_064_a_call_without_a_plan_goes_back_to_the_session():
    """AG-SESSION-064 — tests/brd/tg_agent_shell/agents.feature"""
    provider = ScriptedProvider(
        [
            mutation_turn(("read", {}), content=""),
            mutation_turn(("read", {}), content="The words say lunch; I call read."),
        ]
    )

    assert await _read(provider) == "read"

    first, second = provider.requests
    assert first.messages[0] == {"role": "system", "content": f"Read the words.\n{PLAN_FIRST}"}
    [refused] = [message for message in second.messages if message["role"] == "tool"]
    assert json.loads(refused["content"]) == {
        "status": "error",
        "code": "plan_required",
        "error": "The call came without a plan.",
        "hint": "Write your plan in one to three short lines first, then call one of: read.",
        "retryable": True,
    }


async def test_ag_session_064_calls_without_a_plan_spend_the_repairs():
    """AG-SESSION-064 — tests/brd/tg_agent_shell/agents.feature"""
    provider = ScriptedProvider(
        [mutation_turn(("read", {}), content="")] * (MINI_SESSION_REPAIR_ROUNDS + 1)
    )

    with pytest.raises(MiniSessionError, match="without a plan"):
        await _read(provider)
    assert len(provider.requests) == MINI_SESSION_REPAIR_ROUNDS + 1


async def test_ag_session_064_a_session_not_asked_to_plan_takes_a_bare_call():
    """AG-SESSION-064 — tests/brd/tg_agent_shell/agents.feature"""
    provider = ScriptedProvider([mutation_turn(("read", {}), content="")])

    assert await _read(provider, plan_required=False) == "read"
    assert provider.requests[0].messages[0] == {"role": "system", "content": "Read the words."}
