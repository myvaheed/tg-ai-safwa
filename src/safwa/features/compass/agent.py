"""The tool that hands a subagent the method for one item kind, in the wording each
feature's check on a new item reads."""

from __future__ import annotations

import json
from typing import Any

from llm_gateway import ToolCall
from tg_agent_shell.ai.mini import ReadToolSpec

from ...foundation.method import Method, explanation
from ..cards.api import ACTION, GOAL
from ..checks.api import CHECK
from ..values.api import LINK, VALUE

# What the onboarding subagent may ask about, by the word it asks with.
TOPICS: dict[str, Method] = {
    "values": VALUE,
    "goals": GOAL,
    "actions": ACTION,
    "checks": CHECK,
    "links": LINK,
}

MORE_ABOUT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_more_about",
        "description": (
            "Read how Safwa words a good Value, Goal, Action, Check or Value link: "
            "what it is, its criteria, how to write one, and examples."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "enum": list(TOPICS)},
            },
            "required": ["topic"],
        },
    },
}


async def _more_about(call: ToolCall) -> dict[str, Any]:
    try:
        topic = json.loads(call.arguments_json or "{}").get("topic")
    except (json.JSONDecodeError, AttributeError):
        topic = None
    if topic not in TOPICS:
        return {
            "status": "error",
            "error": f"There is no topic {topic!r}.",
            "hint": f"Retry with topic one of: {', '.join(TOPICS)}.",
        }
    return {"topic": topic, "text": explanation(TOPICS[topic])}


MORE_ABOUT = ReadToolSpec(MORE_ABOUT_TOOL, _more_about)
