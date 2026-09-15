"""Small service operations plugged into the real hook registry by UI tests."""

from __future__ import annotations

from tg_agent_shell.hooks.contracts import HookRegistration, HookSpec, OnAfterTurn, Run
from tg_agent_shell.hooks.registry import HookRegistry


def run_hooks(*operations) -> HookRegistry:
    async def candidate(event):
        return (event,)

    return HookRegistry.of(
        tuple(
            HookRegistration(HookSpec(
                name=operation.__name__, owner="test", on=(OnAfterTurn(),),
                evaluate=candidate, effect=Run(operation),
            ))
            for operation in operations
        ),
        owners=frozenset({"test"}),
    )
