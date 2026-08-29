"""A model session that can suspend on a person and be resumed.

The loop, the routed chain and the record of a session live here. What a tool does, what a
change is, and where the state is kept are the application's, through `ports`.
"""

from __future__ import annotations

from .context import append_user_message, cache_breakpoint, system_note
from .loop import ToolBudgetExceeded, run_loop
from .manager import AgentManager, failure_reason
from .model import (
    AgentDefinition,
    AgentLoopResult,
    AgentSession,
    PendingTool,
    RunRecord,
    RunStatus,
    ToolOutcome,
    TurnOutcome,
    json_safe,
    log_preview,
    resumed_transcript,
    route_receipt,
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
    "Materializer",
    "Observer",
    "PendingTool",
    "RunRecord",
    "RunStatus",
    "SessionStore",
    "ToolBudgetExceeded",
    "ToolOutcome",
    "ToolRunner",
    "TurnOutcome",
    "append_user_message",
    "cache_breakpoint",
    "failure_reason",
    "json_safe",
    "log_preview",
    "resumed_transcript",
    "route_receipt",
    "run_loop",
    "system_note",
]
