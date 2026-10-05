"""Routed subagents: the Advisor hands them the work, and they hand it back done.

A routed subagent is a session of the same shape as the Advisor's — its own prompt, its own
tools, its own transcript — reading the same conversation.  What it proposes is the review
screen; what it writes goes back to the Advisor, which forwards it to the owner as it is or
answers in its own words.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
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
    # It answers questions as well as doing work, so its first step may be words.
    answers_questions: bool = False
    # Its own current values, read again at every step and put after the dialogue, never
    # into the cached prefix.
    current: Callable[[], Awaitable[str]] | None = field(default=None)
    # The item types its `open` may put on the screen. Declaring none withholds the tool.
    opens: tuple[str, ...] = ()
