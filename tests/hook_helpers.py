"""Small service operations plugged into the real hook registry by UI tests."""

from __future__ import annotations

from tg_agent_shell.hooks.contracts import HookSpec, OnAfterTurn, Run
from tg_agent_shell.hooks.registry import HookRegistry


def run_hooks(*operations) -> HookRegistry:
    async def candidate(event):
        return (event,)

    return HookRegistry.of(
        tuple(
            HookSpec(
                name=operation.__name__, owner="test", on=(OnAfterTurn(),),
                evaluate=candidate, effect=Run(operation),
                title=operation.__name__, description="Test work after a turn.",
            )
            for operation in operations
        ),
        owners=frozenset({"test"}),
    )
