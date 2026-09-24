"""Small service operations plugged into the real hook registry by UI tests."""

from __future__ import annotations

from tg_agent_shell.foundation.changes import Committed, take_changes
from tg_agent_shell.hooks.contracts import HookSpec, OnAfterTurn, OnBeforeTurn, Run
from tg_agent_shell.hooks.registry import HookRegistry


def changes_of(session, *kinds: str) -> list[Committed]:
    """The facts of these kinds a session recorded, taken off it with every other one: the
    rest are other hooks' to follow."""
    return [change for change in take_changes(session.info) if change.kind in kinds]


def run_hooks(*operations) -> HookRegistry:
    return _hooks(OnAfterTurn(), "Test work after a turn.", operations)


def before_turn_hooks(*operations) -> HookRegistry:
    return _hooks(OnBeforeTurn(), "Test work before a turn.", operations)


def _hooks(subscription, description: str, operations) -> HookRegistry:
    async def candidate(event):
        return (event,)

    return HookRegistry.of(
        tuple(
            HookSpec(
                name=operation.__name__, owner="test", on=(subscription,),
                evaluate=candidate, effect=Run(operation),
                title=operation.__name__, description=description,
            )
            for operation in operations
        ),
        owners=frozenset({"test"}),
    )
