"""Routed subagents: the Advisor hands its turn over, and they finish it themselves.

A routed subagent is a session of the same shape as the Advisor's — its own prompt, its own
tools, its own transcript — reading the same conversation.  What it writes is the chat
message, and what it proposes is the review screen, with nothing relayed in between.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .mini import ReadToolSpec
from .sql import ReadOnlyQueryRunner

# How much of the conversation a subagent reads, unless it declares its own window.
SUBAGENT_HISTORY_LAST_MESSAGES = 10


@dataclass(frozen=True)
class RoutedSubagent:
    """One subagent `route(name)` can hand the turn to."""

    name: str
    # The whole of what this subagent reads before the conversation, composed by the
    # application: no voice of any product is written here.
    prompt: str
    # `query_data` for this reader alone, over the views it declared. The prompt says what
    # they are and this refuses everything else, out of the one declaration.
    query_runner: ReadOnlyQueryRunner | None = None
    read_tools: tuple[ReadToolSpec, ...] = ()
    mutation_tools: tuple[str, ...] = ()
    # Whether the workspace's current state belongs in its context at all.
    workspace_state: bool = False
    # How many of the conversation's newest messages it reads.
    history_messages: int = SUBAGENT_HISTORY_LAST_MESSAGES
    # Its words are the work: they reach the owner as they are, inside the Advisor's own
    # message, instead of being retold.
    shown_as_is: bool = False
    # The volatile line that goes after the dialogue, never into the cached prefix.
    clock: Callable[[], str] | None = field(default=None)
