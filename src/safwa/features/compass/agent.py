"""Compass as the Advisor routes to it: a subagent that reads the owner's whole tree, asks
about what could be set better and shows the tree as one picture; and the tool that hands it
— or onboarding — the method for one item kind, in the wording each feature's check on a new
item reads."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile
from sqlalchemy import select

from llm_gateway import ToolCall
from tg_agent_shell.ai.contracts import ToolResultStatus
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import owner_anchor
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...foundation.method import Method, bullets, explanation, lines
from ..cards.api import ACTION, GOAL
from ..cards.model import TERMINAL_STAGES, Card, CardKind
from ..checks.api import CHECK
from ..schedules.api import workspace_zone
from ..values.api import LINK, VALUE
from ..values.model import Value
from .charts import draw_sky
from .constellation import Detail, read_sky
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

SHOW_CONSTELLATION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "show_constellation",
        "description": (
            "Send the user's tree to the chat as one picture: the Values as stars above, "
            "Goals and Subgoals under them, Actions at the bottom, and what Compass would ask "
            "about ringed in red."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                    "description": "Only the part of the tree one Value is on, by its name.",
                },
                "goal": {
                    "type": "integer",
                    "description": "Only one Goal's branch, by its id.",
                },
                "detail": {
                    "type": "string",
                    "enum": [detail.value for detail in Detail],
                    "description": (
                        "goals: Values, Goals and Subgoals. actions: and Actions; the default. "
                        "all: and Checks."
                    ),
                },
                "done": {
                    "type": "boolean",
                    "description": "Also the finished Goals and Actions, drawn faint.",
                },
            },
            "required": [],
        },
    },
}


def _refused(error: str, hint: str) -> dict[str, Any]:
    return {
        "status": ToolResultStatus.ERROR.value,
        "code": "invalid_arguments",
        "error": error,
        "hint": hint,
        "retryable": True,
    }


def show_constellation_tool(context: AgentContext) -> ReadToolSpec:
    """`show_constellation` for an application that hands its read tools the chat and the bot."""
    chat, bot = context.chat, context.bot

    async def show_constellation(call: ToolCall) -> dict[str, Any]:
        try:
            arguments = json.loads(call.arguments_json or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        detail = arguments.get("detail", Detail.ACTIONS.value)
        named = arguments.get("value")
        goal = arguments.get("goal")
        done = bool(arguments.get("done"))
        if detail not in {each.value for each in Detail}:
            return _refused(
                f"There is no detail {detail!r}.",
                f'Retry with "detail" one of: {", ".join(Detail)}, or without it.',
            )
        if named and goal is not None:
            return _refused("Give value or goal, not both.", "Retry with one of them, or none.")
        async with context.sessions() as session:
            value = None
            if named:
                values = {
                    each.name.casefold(): each
                    for each in await session.scalars(select(Value).order_by(Value.order))
                }
                if str(named).casefold() not in values:
                    return _refused(
                        f"There is no Value named {named!r}.",
                        f'Retry with "value" one of: {", ".join(each.name for each in values.values())}.',
                    )
                value = values[str(named).casefold()].id
            if goal is not None:
                card = await session.get(Card, goal) if isinstance(goal, int) else None
                if card is None or card.kind != CardKind.GOAL.value or card.archived_at is not None:
                    return _refused(
                        f"There is no Goal with id {goal!r}.",
                        "Find its id in ai_cards with kind = 'goal', or call without goal.",
                    )
                if card.effective_stage in {stage.value for stage in TERMINAL_STAGES} and not done:
                    return _refused("That Goal is finished.", 'Retry with "done": true.')
            sky = await read_sky(session, value=value, goal=goal, detail=Detail(detail), done=done)
            today = utcnow().astimezone(await workspace_zone(session)).date()
        if sky is None:
            return {"sent": None, "note": "Nothing to draw yet: no Value and no open Goal or Action."}
        anchor = owner_anchor(bot, context.owner_id)
        await bot.send_chat_action(anchor.chat.id, ChatAction.UPLOAD_DOCUMENT)
        picture = await asyncio.to_thread(draw_sky, sky, today)
        # A document keeps every pixel, so the owner can zoom into a wide tree.
        await chat.send_document(
            anchor,
            BufferedInputFile(picture, filename="constellation.png"),
            kind=MessageKind.RECEIPT.value,
        )
        return {
            "sent": sky.title,
            "next": "The picture is in the chat. Do not call show_constellation again. Answer "
            "in one short line.",
        }

    return ReadToolSpec(SHOW_CONSTELLATION_TOOL, show_constellation)

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
Asked to see the tree, or the part of it one Value or Goal is on, as a picture: call `show_constellation`.
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
    return "\n".join(["Found without a model:", *(f"- {finding.line}" for finding in found)])


COMPASS_AGENT = AgentSpec(
    name="compass",
    purpose=(
        "the user asks whether their own Values, Goals, Actions, Checks or the links between "
        "them are set right, which Value an item serves, or where an item belongs in the tree; "
        "asks to see the tree, or a part of it, as a picture; "
        'or a request starts with "Compass.".'
    ),
    instructions=COMPASS_PROMPT,
    views=("ai_cards", "ai_checks", "ai_values", "ai_tags"),
    workspace_state=True,
    read_tools=lambda context: (MORE_ABOUT, show_constellation_tool(context)),
    current=compass_now,
    answers_questions=True,
)
