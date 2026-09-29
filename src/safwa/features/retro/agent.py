"""The retro subagent: it finds an ended Sprint by its number or a day in it, answers from
the records the Sprints left, and puts one retro on the screen.

It reads no view: its three tools are its reads, and a sum or a mean is worked out by
`get_aggregate`, never by the model. The newest Sprints that ended are the block after the
conversation, read again at every step.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from llm_gateway import ToolCall
from tg_agent_shell.ai.contracts import ToolResultStatus
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...constants import WEEKDAY_NAMES
from ...foundation.workspace import require_workspace
from ..planning.closing import RetroStatistics
from ..planning.model import Sprint
from .records import (
    RETRO_DATA_MAX,
    aggregate,
    ended_sprints,
    records_by_number,
    retro_link,
    sprint_on,
)

# How many of the newest Sprints that ended the block after the conversation names.
RETRO_RECENT_SPRINTS = 5

RETRO_PROMPT = f"""You answer questions about the Sprints that ended, from the records they left, and you put one Sprint's retro on the screen.

# Finding a Sprint
- The last message lists the newest Sprints that ended: each one's retro link, its number and the days it ran.
- A Sprint named by a date: call `get_retro_number` with the date as YYYY-MM-DD.
- A number is written like 26.09-01. Pass it exactly so.
- A running Sprint has no retro yet. Say so.

# Answering from the records
- What one Sprint or a few added up to: `get_retro_data` with their numbers, at most {RETRO_DATA_MAX} a call.
- A total or an average over several Sprints: `get_aggregate` with their numbers and `op` "sum" or "mean".
- Never add up, divide or round a number yourself. Give the number the tool returned.
- When the result has `counted_over`, say over how many Sprints that number is.
- Name every Sprint by its retro link, as the tools write it: `[Sprint 26.09-01 retro](retro:12)`.

# Showing a retro
When the user asks to see or open one Sprint's retro, call `open` with `item_type` "retro" and that Sprint's id. Then answer in one short line.

# What you cannot do
- Mark the Success criteria met or not met, or analyse a Sprint: the user does it on the retro screen.
- Answer about the running Sprint: that is not yours.

# Answering
Your answer goes to the user as you wrote it. Keep it short."""

_NUMBERS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Sprint numbers, each like 26.09-01.",
}

GET_RETRO_NUMBER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_retro_number",
        "description": "The ended Sprint that was running on one date: its number and id.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "The local date as YYYY-MM-DD."}
            },
            "required": ["date"],
        },
    },
}

GET_RETRO_DATA_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_retro_data",
        "description": (
            f"What each named ended Sprint added up to, by its number. At most "
            f"{RETRO_DATA_MAX} numbers a call."
        ),
        "parameters": {
            "type": "object",
            "properties": {"numbers": _NUMBERS},
            "required": ["numbers"],
        },
    },
}

GET_AGGREGATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_aggregate",
        "description": (
            "The sum or the mean of every number the named ended Sprints' records carry, "
            "as one object."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numbers": _NUMBERS,
                "op": {
                    "type": "string",
                    "enum": ["sum", "mean"],
                    "description": "sum for a total, mean for an average.",
                },
            },
            "required": ["numbers", "op"],
        },
    },
}


def _error(call: ToolCall, error: str, hint: str) -> dict[str, Any]:
    return {
        "status": ToolResultStatus.ERROR.value,
        "code": "invalid_arguments",
        "error": error,
        "hint": hint,
        "retryable": True,
    }


def _arguments(call: ToolCall) -> dict[str, Any]:
    try:
        arguments = json.loads(call.arguments_json or "{}")
    except json.JSONDecodeError:
        return {}
    return arguments if isinstance(arguments, dict) else {}


def _numbers(call: ToolCall) -> list[str] | None:
    numbers = _arguments(call).get("numbers")
    if isinstance(numbers, str):
        numbers = [numbers]
    if not isinstance(numbers, list) or not numbers:
        return None
    return [str(number) for number in numbers]


def _retro_read_tools(context: AgentContext) -> tuple[ReadToolSpec, ...]:
    sessions = context.sessions

    async def get_retro_number(call: ToolCall) -> dict[str, Any]:
        raw = str(_arguments(call).get("date") or "").strip()
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            return _error(
                call, f"{raw!r} is not a date.", 'Retry with {"date": "YYYY-MM-DD"}.'
            )
        async with sessions() as session:
            workspace = await require_workspace(session)
            today = utcnow().astimezone(ZoneInfo(workspace.timezone)).date()
            return await sprint_on(session, day, today)

    async def get_retro_data(call: ToolCall) -> dict[str, Any]:
        numbers = _numbers(call)
        if numbers is None:
            return _error(
                call, "No Sprint numbers were given.", 'Retry with {"numbers": ["26.09-01"]}.'
            )
        if len(numbers) > RETRO_DATA_MAX:
            return _error(
                call,
                f"{len(numbers)} numbers is more than {RETRO_DATA_MAX} at once.",
                f"Ask for at most {RETRO_DATA_MAX}, then call again for the rest. For a total "
                "or an average, call get_aggregate instead.",
            )
        async with sessions() as session:
            return await records_by_number(session, numbers)

    async def get_aggregate(call: ToolCall) -> dict[str, Any]:
        numbers = _numbers(call)
        op = _arguments(call).get("op")
        if numbers is None or op not in ("sum", "mean"):
            return _error(
                call,
                "get_aggregate needs Sprint numbers and op sum or mean.",
                'Retry with {"numbers": ["26.09-01", "26.08-02"], "op": "mean"}.',
            )
        async with sessions() as session:
            return aggregate(await records_by_number(session, numbers), op)

    return (
        ReadToolSpec(GET_RETRO_NUMBER_TOOL, get_retro_number),
        ReadToolSpec(GET_RETRO_DATA_TOOL, get_retro_data),
        ReadToolSpec(GET_AGGREGATE_TOOL, get_aggregate),
    )


_MARK = {True: "met", False: "not met", None: "not marked"}


async def retro_now(context: AgentContext) -> str:
    """The newest Sprints that ended, and the one running, for this step."""
    async with context.sessions() as session:
        workspace = await require_workspace(session)
        today = utcnow().astimezone(ZoneInfo(workspace.timezone)).date()
        lines = [f"Today is {today.isoformat()} ({WEEKDAY_NAMES[today.weekday()]})."]
        recent = await ended_sprints(session, RETRO_RECENT_SPRINTS)
        if recent:
            lines.append(f"The newest {len(recent)} Sprints that ended, newest first:")
            for sprint in recent:
                statistics = RetroStatistics.from_record(sprint.retro)
                lines.append(
                    f"- {retro_link(sprint)}: ran {statistics.first_day.isoformat()} – "
                    f"{statistics.last_day.isoformat()}, Success criteria "
                    f"{_MARK[sprint.criterion_met]}, "
                    + ("analysed" if sprint.analysis else "not analysed")
                )
        else:
            lines.append("No Sprint has ended yet.")
        running = (
            await session.get(Sprint, workspace.active_sprint_id)
            if workspace.active_sprint_id
            else None
        )
        lines.append(
            f"Running now: Sprint {running.number}. It has no retro until it ends."
            if running is not None
            else "No Sprint is running now."
        )
        return "\n".join(lines)


RETRO_AGENT = AgentSpec(
    name="retro",
    purpose=(
        "a question about the Sprints that ended — their numbers, dates, results, totals and "
        "averages — or showing the retro of one of them."
    ),
    instructions=RETRO_PROMPT,
    read_tools=_retro_read_tools,
    current=retro_now,
    opens=("retro",),
    shown_as_is=True,
)
