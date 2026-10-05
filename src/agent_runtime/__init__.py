"""A model session that can suspend on a person and be resumed.

The loop, the routed chain and the record of a session live here. What a tool does, what a
change is, and where the state is kept are the application's, through `ports`.
"""

from __future__ import annotations

from .context import append_user_message, system_note
from .loop import RUNTIME_TOOLS, ToolBudgetExceeded
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
    flatten_content,
    json_safe,
    log_preview,
    routed_answers,
)
from .ports import ContextSource, Materializer, Observer, SessionStore, ToolRunner
from .testing import InMemorySessionStore

__all__ = [
    "RUNTIME_TOOLS",
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
    "flatten_content",
    "json_safe",
    "log_preview",
    "routed_answers",
    "system_note",
]
