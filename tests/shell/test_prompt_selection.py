from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from tg_agent_shell.ai.messages import ContextBuilder, StateBlocks
from tg_agent_shell.ai.subagents import RoutedSubagent


@pytest.mark.parametrize("titled", [False, True])
async def test_selected_prompt_is_the_only_system_message_and_is_read_each_step(titled):
    @asynccontextmanager
    async def sessions():
        yield None

    async def memory():
        return SimpleNamespace(text="")

    async def state(_session):
        return StateBlocks(state="", clock="Now.")

    async def selected_prompt():
        return policy

    agent = RoutedSubagent(
        name="ledger", prompt=selected_prompt, title="Ledger" if titled else None,
    )
    builder = ContextBuilder(
        sessions, SimpleNamespace(sync=memory), state, system_prompt="Root.",
        subagents={agent.name: agent},
    )
    for policy in ("First policy.", "Second policy."):
        messages = await builder.messages_for(agent.name, [])
        assert [message for message in messages if message["role"] == "system"] == [
            {"role": "system", "content": policy}
        ]
