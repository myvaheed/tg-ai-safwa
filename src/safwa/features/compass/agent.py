"""Compass as the Advisor routes to it: a subagent that reads the owner's whole tree and asks
about what could be set better, and the tool that hands it — or onboarding — the method for
one item kind, in the wording each feature's check on a new item reads."""

from __future__ import annotations

import json
from typing import Any

from llm_gateway import ToolCall
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...foundation.method import Method, bullets, explanation, lines
from ..cards.api import ACTION, GOAL
from ..checks.api import CHECK
from ..values.api import LINK, VALUE
from .graph import COMPASS_FINDINGS_MAX, NOT_PROBLEMS, PROBLEMS, plain_findings, read_tree

# What `get_more_about` may be asked about, by the word it asks with.
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

COMPASS_PROMPT = f"""You help the user see whether their own Values, Goals, Actions and Checks are set and linked right. You read; you change nothing.

{{items}}

# What could be set better
{bullets(PROBLEMS)}{lines(NOT_PROBLEMS)}Your current values list what SQL found; those are facts.

# A question about the user's tree
Read what you need with `query_data` before you answer.
Answer about the items asked about, or about the whole tree when asked about all of it.
Name at most {COMPASS_FINDINGS_MAX} findings, the most important first. Cite each item, and ask one short question about each.
Offer every change as a possibility. The user decides; leaving it as it is is fine.
Asked what makes a good Value, Goal, Action, Check or Value link: call `get_more_about` with that topic.
Nothing stands out: say the tree looks right.
Never promise to change anything yourself. A change is proposed once the user agrees.
Your answer goes to the user as you wrote it.

# A Compass request: the whole tree was looked at
The newest message starts with "Compass." and lists what was found. Other requests in it are not yours.
Read the current items first; leave out a finding that no longer holds.
Say you looked at the whole tree again and some things could be set better.
Name each finding in one short line, citing its items, then ask whether to go through them together now.
None still holds: say so in one line.

# Read the data
`query_data` runs one read-only `SELECT` or `WITH ... SELECT` over these views only.

{{views}}"""


async def compass_now(context: AgentContext) -> str:
    """What SQL finds in the tree, for this step."""
    async with context.sessions() as session:
        found = plain_findings(await read_tree(session))
    if not found:
        return "Found without a model: nothing."
    return "\n".join(["Found without a model:", *(f"- {line}" for line in found)])


COMPASS_AGENT = AgentSpec(
    name="compass",
    purpose=(
        "the user asks whether their own Values, Goals, Actions, Checks or the links between "
        "them are set right, which Value an item serves, or where an item belongs in the tree; "
        'or a request starts with "Compass.".'
    ),
    instructions=COMPASS_PROMPT,
    views=("ai_cards", "ai_checks", "ai_values", "ai_tags"),
    workspace_state=True,
    read_tools=lambda context: (MORE_ABOUT,),
    current=compass_now,
    answers_questions=True,
)
