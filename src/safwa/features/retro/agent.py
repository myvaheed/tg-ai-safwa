"""The retro subagent: it finds an ended Sprint by its number or a day in it, answers from
the records the Sprints left, puts one retro on the screen, sends the charts of any of them,
and sends a Life in weeks picture with Life's own tool.

Its reads choose Sprints by number, by two dates, or all of them; a call that mixes those up
is answered with the call to make instead, built from what it sent.

It reads no view: its tools are its reads, and a sum or a mean is worked out by
`get_aggregate`, never by the model. `show_charts` draws and sends the charts itself, the
same pictures as the retro's button, so the model only says in a line what they are. The
newest Sprints that ended are the block after the conversation, read again at every step.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import ToolCall
from tg_agent_shell.ai.contracts import ToolResultStatus
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import owner_anchor
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...constants import WEEKDAY_NAMES
from ...foundation.workspace import require_workspace
from ..life.agent import show_life_tool
from ..planning.closing import RetroStatistics
from ..planning.model import Sprint
from ..profile.api import effort_tracking_on
from .charts import CHARTS, ChartSprint, covered, render_charts
from .records import (
    RETRO_DATA_MAX,
    aggregate,
    ended_between,
    ended_span,
    ended_sprints,
    retro_link,
    sprint_records,
    sprints_by_number,
)

# How many of the newest Sprints that ended the block after the conversation names.
RETRO_RECENT_SPRINTS = 5

CHART_NAMES = tuple(name for name, _ in CHARTS)

RETRO_PROMPT = f"""You answer questions about the Sprints that ended, from the records they left, and you put one Sprint's retro on the screen.

# Finding a Sprint
- The last message lists the newest Sprints that ended: each one's retro link, its number and the days it ran.
- A Sprint named by a date: give that date as both `start_date` and `end_date`.
- A number is written like 26.09-01. Pass it exactly so.
- A running Sprint has no retro yet. Say so.

# Answering from the records
- Choose Sprints one way: `numbers`, or `start_date` and `end_date` as YYYY-MM-DD, or neither for every Sprint that ended.
- Dates choose every Sprint with a day between them, whole.
- What one Sprint or a few added up to: `get_retro_data`, at most {RETRO_DATA_MAX} Sprints a call.
- A total or an average over several Sprints: `get_aggregate` with `op` "sum" or "mean".
- Never add up, divide or round a number yourself. Give the number the tool returned.
- When the result has `counted_over`, say over how many Sprints that number is.
- Name every Sprint by its retro link, as the tools write it: `[Sprint 26.09-01 retro](retro:12)`.

# Showing a retro
When the user asks to see or open one Sprint's retro, call `open` with `item_type` "retro" and that Sprint's id. Then answer in one short line.

# Charts
- When the user asks for charts, a graph or a picture of Sprints: call `show_charts`, choosing the Sprints as above.
- One chart asked for: add `chart` with its name.
- The tool sends the charts itself. Then answer in one short line.

# Life in weeks
- When the user asks for Life in weeks, the weeks of their life or their life grid: call `show_life`, not `show_charts`.
- One picture asked for: add `chart`. One Category, Energy type or Value alone: add `category`, `energy` or `value`.
- The tool sends the picture itself. Then answer in one short line.

# What you cannot do
- Mark the Success criteria met or not met, or analyse a Sprint: the user does it on the retro screen.
- Set the birth date or the years of Life in weeks: the user does it in its Settings.
- Answer about the running Sprint: that is not yours.

# Answering
Your answer goes to the user as you wrote it. Keep it short."""

_CHOICE = {
    "numbers": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Sprint numbers, each like 26.09-01. Leave out with dates.",
    },
    "start_date": {
        "type": "string",
        "description": "The first local date as YYYY-MM-DD. Only with end_date.",
    },
    "end_date": {
        "type": "string",
        "description": "The last local date as YYYY-MM-DD. Only with start_date.",
    },
}

GET_RETRO_DATA_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_retro_data",
        "description": (
            "What each chosen ended Sprint added up to, by its number. At most "
            f"{RETRO_DATA_MAX} Sprints a call."
        ),
        "parameters": {"type": "object", "properties": _CHOICE, "required": []},
    },
}

SHOW_CHARTS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "show_charts",
        "description": "Send the charts of the chosen ended Sprints to the chat, or one chart.",
        "parameters": {
            "type": "object",
            "properties": {
                **_CHOICE,
                "chart": {
                    "type": "string",
                    "enum": list(CHART_NAMES),
                    "description": (
                        "One chart; leave out for all. burnup: Actions finished day by day. "
                        "velocity: taken and finished per Sprint. capacity: plan and capacity. "
                        "category: by Category. mix: Category mix per Sprint. energy: by "
                        "Energy type. category_energy: each Category's Energy types. time: "
                        "tracked time. checks: Checks passed and missed. week: by weekday."
                    ),
                },
            },
            "required": [],
        },
    },
}

GET_AGGREGATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_aggregate",
        "description": (
            "The sum or the mean of every number the chosen ended Sprints' records carry, "
            "as one object."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **_CHOICE,
                "op": {
                    "type": "string",
                    "enum": ["sum", "mean"],
                    "description": "sum for a total, mean for an average.",
                },
            },
            "required": ["op"],
        },
    },
}


def _error(error: str, hint: str) -> dict[str, Any]:
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


def _numbers(arguments: dict[str, Any]) -> list[str] | None:
    numbers = arguments.get("numbers")
    if isinstance(numbers, str):
        numbers = [numbers]
    if not isinstance(numbers, list) or not numbers:
        return None
    return [str(number) for number in numbers]


class _Instead(Exception):
    """What a read answers when its arguments choose no records: a refusal with the call to
    make instead, or word that no Sprint is among the chosen."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(result)
        self.result = result


async def _today(session: AsyncSession) -> date:
    workspace = await require_workspace(session)
    return utcnow().astimezone(ZoneInfo(workspace.timezone)).date()


def _day(raw: str, key: str, today: date) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise _Instead(
            _error(
                f"{key} {raw!r} is not a date.",
                f'Retry with "{key}" as YYYY-MM-DD, like "{today}".',
            )
        ) from None


async def _chosen(session: AsyncSession, arguments: dict[str, Any]) -> dict[str, Sprint | str]:
    """The Sprints the arguments choose by their numbers, or why a number has none: by
    number, every Sprint with a day between two dates, or every Sprint that ended."""
    numbers = _numbers(arguments)
    start, end = (str(arguments.get(key) or "").strip() for key in ("start_date", "end_date"))
    if numbers and (start or end):
        raise _Instead(
            _error(
                "Give numbers, or start_date and end_date, not both.",
                f"Retry with {json.dumps({'numbers': numbers})}, or with start_date and "
                "end_date and no numbers.",
            )
        )
    if numbers:
        return await sprints_by_number(session, numbers)
    if not (start or end):
        ended = await ended_sprints(session)
        if not ended:
            raise _Instead({"sprint_count": 0, "note": "No Sprint has ended yet."})
        return {sprint.number: sprint for sprint in ended}
    span = await ended_span(session)
    today = await _today(session)
    if not (start and end):
        dates = (
            {"start_date": start, "end_date": today.isoformat()}
            if start
            else {"start_date": (span[0] if span else today).isoformat(), "end_date": end}
        )
        raise _Instead(
            _error("start_date and end_date go together.", f"Retry with {json.dumps(dates)}.")
        )
    first, last = _day(start, "start_date", today), _day(end, "end_date", today)
    if first > last:
        swapped = {"start_date": last.isoformat(), "end_date": first.isoformat()}
        raise _Instead(
            _error("start_date is after end_date.", f"Retry with {json.dumps(swapped)}.")
        )
    chosen = await ended_between(session, first, last)
    if not chosen:
        note = f"No Sprint that ended has a day from {first} to {last}."
        if span is not None:
            note += f" The Sprints that ended ran from {span[0]} to {span[1]}."
        raise _Instead({"sprint_count": 0, "note": note})
    return {sprint.number: sprint for sprint in chosen}


def _retro_read_tools(context: AgentContext) -> tuple[ReadToolSpec, ...]:
    sessions = context.sessions

    async def get_retro_data(call: ToolCall) -> dict[str, Any]:
        async with sessions() as session:
            try:
                records = await sprint_records(session, await _chosen(session, _arguments(call)))
            except _Instead as instead:
                return instead.result
        if len(records) > RETRO_DATA_MAX:
            return _error(
                f"{len(records)} Sprints is more than {RETRO_DATA_MAX} at once: "
                f"{', '.join(records)}.",
                f"Call again with at most {RETRO_DATA_MAX} of these numbers. For a total or an "
                "average, call get_aggregate with the same arguments.",
            )
        return records

    async def get_aggregate(call: ToolCall) -> dict[str, Any]:
        arguments = _arguments(call)
        op = arguments.get("op")
        if op not in ("sum", "mean"):
            return _error(
                f"op {op!r} is neither sum nor mean.",
                'Retry with "op": "mean" for an average or "sum" for a total.',
            )
        async with sessions() as session:
            try:
                return aggregate(await sprint_records(session, await _chosen(session, arguments)), op)
            except _Instead as instead:
                return instead.result

    reads = (
        ReadToolSpec(GET_RETRO_DATA_TOOL, get_retro_data),
        ReadToolSpec(GET_AGGREGATE_TOOL, get_aggregate),
    )
    chat, bot = context.chat, context.bot
    if chat is None or bot is None:
        return reads

    async def show_charts(call: ToolCall) -> dict[str, Any]:
        arguments = _arguments(call)
        chart = arguments.get("chart")
        if chart is not None and chart not in CHART_NAMES:
            return _error(
                f"There is no chart named {chart!r}.",
                f'Retry with "chart" one of: {", ".join(CHART_NAMES)}, or without it.',
            )
        async with sessions() as session:
            try:
                chosen = await _chosen(session, arguments)
            except _Instead as instead:
                return instead.result
            missing = [why for why in chosen.values() if isinstance(why, str)]
            if missing:
                return _error(" ".join(missing), "Retry with the numbers of Sprints that ended.")
            # Sprints run one at a time, so the order they were made in is the order they ended.
            sprints = [
                ChartSprint.of(sprint)
                for sprint in sorted(
                    (sprint for sprint in chosen.values() if isinstance(sprint, Sprint)),
                    key=lambda sprint: sprint.id,
                )
            ]
            effort_tracking = await effort_tracking_on(session)
        anchor = owner_anchor(bot, context.owner_id)
        await bot.send_chat_action(anchor.chat.id, ChatAction.UPLOAD_PHOTO)
        pictures = await asyncio.to_thread(render_charts, sprints, effort_tracking=effort_tracking)
        if chart is not None:
            drawn = [name for name, _ in pictures]
            pictures = [picture for picture in pictures if picture[0] == chart]
            if not pictures:
                return _error(
                    f"These Sprints have no {chart} chart.",
                    f'Retry with "chart" one of: {", ".join(drawn)}, or without it.',
                )
        # Not a screen kind, so they stay in the chat like a message when the next screen
        # comes; and not a conversation kind, so the model reads no words for them.
        await chat.send_photos(
            anchor,
            [BufferedInputFile(png, filename=f"{name}.png") for name, png in pictures],
            kind=MessageKind.RECEIPT.value,
        )
        return {
            "sent": covered(sprints),
            "charts": [name for name, _ in pictures],
            "next": "The charts are in the chat. Do not call show_charts again. Answer in one "
            "short line.",
        }

    return (*reads, ReadToolSpec(SHOW_CHARTS_TOOL, show_charts), show_life_tool(context))


_MARK = {True: "met", False: "not met", None: "not marked"}


async def retro_now(context: AgentContext) -> str:
    """The newest Sprints that ended, and the one running, for this step."""
    async with context.sessions() as session:
        workspace = await require_workspace(session)
        today = await _today(session)
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
        "a question about a Sprint that ended — its number, dates, Success criteria, how "
        "many Actions or EP it took and finished, totals and averages over several — or "
        "showing its retro, its charts, or Life in weeks: the weeks of the user's life as one "
        "picture."
    ),
    instructions=RETRO_PROMPT,
    read_tools=_retro_read_tools,
    current=retro_now,
    opens=("retro",),
    answers_questions=True,
)
