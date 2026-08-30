"""A model session that can suspend on a person and be resumed.

The loop, the routed chain and the record of a session live here. What a tool does, what a
change is, and where the state is kept are the application's, through `ports`.
"""

from __future__ import annotations

from .context import append_user_message, cache_breakpoint, system_note
from .loop import ToolBudgetExceeded
from .manager import AgentManager
from .model import (
    AgentDefinition,
    AgentLoopResult,
    AgentSession,
    InteractionRef,
    PendingTool,
    Resumption,
    RunRecord,
    RunStatus,
    ToolOutcome,
    TurnOutcome,
    json_safe,
    log_preview,
)
from .ports import ContextSource, Materializer, Observer, SessionStore, ToolRunner
from .testing import InMemorySessionStore

__all__ = [
    "AgentDefinition",
    "AgentLoopResult",
    "AgentManager",
    "AgentSession",
    "ContextSource",
    "InMemorySessionStore",
    "InteractionRef",
    "Materializer",
    "Observer",
    "PendingTool",
    "Resumption",
    "RunRecord",
    "RunStatus",
    "SessionStore",
    "ToolBudgetExceeded",
    "ToolOutcome",
    "ToolRunner",
    "TurnOutcome",
    "append_user_message",
    "cache_breakpoint",
    "json_safe",
    "log_preview",
    "system_note",
]
