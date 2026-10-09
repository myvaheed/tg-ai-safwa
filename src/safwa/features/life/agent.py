"""The tool that sends a Life in weeks picture asked for in words; the retro subagent holds it.

It draws and sends the album itself, the same pictures as the Life screen's buttons, so the
model only says in a line what it is. Asked for no picture by name, it sends the first one
with something recorded and names the others; a choice made wrong is answered with the call
to make instead.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile

from llm_gateway import ToolCall
from tg_agent_shell.ai.contracts import ToolResultStatus
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import owner_anchor
from tg_agent_shell.telegram.manifest import AgentContext

from ..cards.model import Category, EnergyType
from .api import life_grid
from .charts import draw_life
from .measures import GROUP_CHARTS, LifeChart, offered, picture
from .records import life_records

_CHARTS = tuple(chart.value for chart in LifeChart)
# What the pictures by Category and by Energy type show alone; a Value goes by its name.
_ALONE = {
    LifeChart.CATEGORY: tuple(kind.value for kind in Category),
    LifeChart.ENERGY: tuple(kind.value for kind in EnergyType),
}

SHOW_LIFE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "show_life",
        "description": (
            "Send one Life in weeks picture to the chat: a square for each week of the user's "
            "life, coloured by one thing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chart": {
                    "type": "string",
                    "enum": list(_CHARTS),
                    "description": (
                        "What colours the weeks; leave out for the first one with records. "
                        "feeling: Diary feeling. actions: Actions finished. effort: Effort "
                        "Points finished. sprints: the Sprints. category, energy, value: what "
                        "the finished Actions carried."
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": list(_ALONE[LifeChart.CATEGORY]),
                    "description": "One Category alone. Only with chart category.",
                },
                "energy": {
                    "type": "string",
                    "enum": list(_ALONE[LifeChart.ENERGY]),
                    "description": "One Energy type alone. Only with chart energy.",
                },
                "value": {
                    "type": "string",
                    "description": "One Value alone, by its name. Only with chart value.",
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


def show_life_tool(context: AgentContext) -> ReadToolSpec:
    """`show_life` for an application that hands its read tools the chat and the bot."""
    chat, bot = context.chat, context.bot

    async def show_life(call: ToolCall) -> dict[str, Any]:
        try:
            arguments = json.loads(call.arguments_json or "{}")
        except json.JSONDecodeError:
            arguments = {}
        raw = arguments.get("chart")
        if raw is not None and raw not in _CHARTS:
            return _refused(
                f"There is no picture named {raw!r}.",
                f'Retry with "chart" one of: {", ".join(_CHARTS)}, or without it.',
            )
        chart = LifeChart(raw) if raw is not None else None
        alone = {
            group: str(arguments[group.value])
            for group in GROUP_CHARTS
            if arguments.get(group.value)
        }
        if len(alone) > 1:
            return _refused(
                "Give one of category, energy and value.", "Retry with one of them, or none."
            )
        focus = None
        if alone:
            [(group, focus)] = alone.items()
            if chart not in (None, group):
                return _refused(
                    f'"{group}" goes with "chart": "{group}".',
                    f"Retry with {json.dumps({'chart': group.value, group.value: focus})}.",
                )
            chart = group
            if group in _ALONE and focus not in _ALONE[group]:
                return _refused(
                    f"There is no {group} named {focus!r}.",
                    f'Retry with "{group}" one of: {", ".join(_ALONE[group])}.',
                )
        async with context.sessions() as session:
            grid = await life_grid(session)
            if grid is None:
                return {
                    "sent": None,
                    "note": "No birth date is set. Tell the user to set it in Retro → "
                    "⏳ Life in weeks → ⚙️ Settings.",
                }
            records = await life_records(session, utcnow())
        charts = offered(records)
        if not charts:
            return {"sent": None, "note": "Nothing is recorded yet to colour the weeks with."}
        chosen = chart or charts[0]
        try:
            shown = picture(chosen, records, grid, focus)
        except DomainError as error:
            if focus is not None and chosen is LifeChart.VALUE:
                return _refused(
                    str(error), f'Retry with "value" one of: {", ".join(records.values)}.'
                )
            return _refused(
                str(error), f'Retry with "chart" one of: {", ".join(charts)}, or without it.'
            )
        anchor = owner_anchor(bot, context.owner_id)
        await bot.send_chat_action(anchor.chat.id, ChatAction.UPLOAD_PHOTO)
        pictures = await asyncio.to_thread(draw_life, shown, grid, records.today, records.since)
        # Not a screen kind, so it stays in the chat like a message when the next screen
        # comes; and not a conversation kind, so the model reads no words for it.
        await chat.send_photos(
            anchor,
            [BufferedInputFile(png, filename=f"{name}.png") for name, png in pictures],
            kind=MessageKind.RECEIPT.value,
        )
        result: dict[str, Any] = {
            "sent": f"Life in weeks · {shown.title}",
            "next": "The picture is in the chat. Do not call show_life again. Answer in one "
            "short line.",
        }
        others = [other.value for other in charts if other is not chosen]
        if chart is None and others:
            result["others"] = others
            result["next"] = (
                "The picture is in the chat. Do not call show_life again. Answer in one short "
                "line that names the other pictures in others."
            )
        return result

    return ReadToolSpec(SHOW_LIFE_TOOL, show_life)
