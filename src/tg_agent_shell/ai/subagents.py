"""Routed subagents: the Advisor hands its turn over, and they finish it themselves.

A routed subagent is a session of the same shape as the Advisor's — its own prompt, its own
tools, its own transcript — reading the same conversation.  What it writes is the chat
message, and what it proposes is the review screen, with nothing relayed in between.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .mini import ReadToolSpec


@dataclass(frozen=True)
class RoutedSubagent:
    """One subagent `route(name)` can hand the turn to."""

    name: str
    # The whole of what this subagent reads before the conversation, composed by the
    # application: no voice of any product is written here.
    prompt: str
    read_tools: tuple[ReadToolSpec, ...] = ()
    mutation_tools: tuple[str, ...] = ()
    # Whether the workspace's current state belongs in its context at all.
    workspace_state: bool = False
    # The volatile line that goes after the dialogue, never into the cached prefix.
    clock: Callable[[], str] | None = field(default=None)
