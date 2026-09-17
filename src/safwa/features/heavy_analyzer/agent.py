"""The heavy analyzer: it writes the query the Advisor could not, and forwards its rows.

It never speaks. A result of fifty rows cannot be retold, and a small model retelling
numbers is where numbers get invented — so the answer is the read itself, untouched.
That makes this a session of `ai/mini.py` rather than a routed subagent: read tools, two
terminal calls, and prose is never accepted.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import Field

from llm_gateway import LlmProvider, ToolCall
from tg_agent_shell.ai.contracts import ToolInput, ToolResultStatus
from tg_agent_shell.ai.mini import (
    MiniSessionError,
    ReadToolSpec,
    TerminalTool,
    run_mini_session,
)
from tg_agent_shell.ai.sql import ReadOnlyQueryRunner
from tg_agent_shell.ai.tools import query_read_tool

NAME = "heavy_analyzer"

# The whole of what the Advisor is ever told about this helper, so it names the tool in the
# shape the tool actually takes.
OFFER = (
    f'This read is complex and was not run. call_helper("{NAME}", "<your question in words>") '
    "writes the query and hands back its result."
)


# Reading is the whole job, so exploring costs more than one turn of the Advisor's does.
# Past this the session gives up and says so rather than reading its way through the workspace.
HEAVY_ANALYZER_MAX_TOOL_CALLS = 10

# Every view, the log of changes included: a question about a stretch of time is what this
# helper exists for, and it is the only reader told that log is there.
VIEWS = (
    "ai_cards",
    "ai_checks",
    "ai_card_events",
    "ai_tags",
    "ai_values",
    "ai_requests",
    "ai_reminders",
    "ai_current_sprint",
    "ai_current_sprint_metrics",
    "ai_diary",
)


PROMPT_TEMPLATE = """You answer one question about Safwa's data with one query.

The Advisor could not write it. Its question is in `<Request from AI>`, and the conversation
before it says what the user actually asked.

# How a turn goes
1. Read with `query_data` until one result answers the question.
2. Call `forward_output`. That last result goes to the Advisor as it is.
3. If you cannot answer it, call `report_failure` with one sentence saying why.

Never write the answer in words. Nothing you type reaches the Advisor — only the rows.
Aim for a result of a few rows: aggregate, group and count rather than listing everything.

# Read the data
`query_data` runs one read-only `SELECT` or `WITH ... SELECT` over these views only.
Every value listed under a view is the lowercase code stored in that column.

{views}
- In `ai_cards` and `ai_checks` a title ending in ` [🔄2, live #7]` is a finished instance and #7 is the open one.
- ` [🔄2]` with no id means the series has ended. ` [📦]` means archived: it still counts.

`created_at` and `updated_at` are UTC text: compare them with `datetime('now')`.
"""


class ForwardOutputInput(ToolInput):
    """The last read is the answer. It takes nothing."""


class ReportFailureInput(ToolInput):
    explanation: str = Field(
        description="One sentence: what you could not work out from the data."
    )


FORWARD_OUTPUT = TerminalTool(
    name="forward_output",
    description="Send your last query_data result to the Advisor as the answer.",
    model=ForwardOutputInput,
)

REPORT_FAILURE = TerminalTool(
    name="report_failure",
    description="You cannot answer the question from the data. Say why in one sentence.",
    model=ReportFailureInput,
)


class _LastRead:
    """What `forward_output` forwards: the newest read that returned data."""

    sql: str = ""
    rows: list[dict[str, Any]] | None = None

    def keep(self, call: ToolCall, rows: Any) -> None:
        # A rejected or broken SELECT is the session's to repair, so it never becomes the
        # answer — otherwise `forward_output` would hand the Advisor an error as a result.
        if not isinstance(rows, list) or _is_error(rows):
            return
        try:
            self.sql = str(json.loads(call.arguments_json or "{}").get("sql", ""))
        except (json.JSONDecodeError, TypeError, AttributeError):
            self.sql = ""
        self.rows = rows


def _is_error(rows: list[Any]) -> bool:
    return bool(rows) and isinstance(rows[0], dict) and rows[0].get("status") == ToolResultStatus.ERROR


def _recording_read_tool(runner: ReadOnlyQueryRunner, last: _LastRead) -> ReadToolSpec:
    """`query_data`, remembering its own newest result so a terminal can forward it."""
    inner = query_read_tool(runner)

    async def read(call: ToolCall) -> Any:
        rows = await inner.run(call)
        last.keep(call, rows)
        return rows

    return ReadToolSpec(inner.schema, read)


async def analyse(
    provider: LlmProvider,
    query_runner: ReadOnlyQueryRunner,
    *,
    prompt: str,
    conversation: str,
    request: str,
) -> dict[str, Any]:
    """Answer one question with rows, or say in one sentence that it could not."""
    last = _LastRead()
    context = f"{conversation}\n<Request from AI>{request}</Request from AI>"
    try:
        result = await run_mini_session(
            provider,
            system_prompt=prompt,
            context=context,
            terminals=(FORWARD_OUTPUT, REPORT_FAILURE),
            read_tools=(_recording_read_tool(query_runner, last),),
            max_tool_calls=HEAVY_ANALYZER_MAX_TOOL_CALLS,
        )
    except MiniSessionError as error:
        return _failed(str(error))
    if result.name == REPORT_FAILURE.name:
        return _failed(str(result.payload.explanation))
    if last.rows is None:
        return _failed("The helper forwarded a result before it had read anything.")
    # The query travels with its rows: `{"status": "passed", "n": 12}` on its own does not
    # say what was counted, and the Advisor is what answers the owner for it.
    return {"helper": NAME, "sql": last.sql, "rows": last.rows}


def build(
    provider: LlmProvider, query_runner: ReadOnlyQueryRunner, *, prompt: str
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Bind the helper to this application, in the shape `call_helper` invokes."""

    async def helper(*, conversation: str, request: str) -> dict[str, Any]:
        return await analyse(
            provider, query_runner, prompt=prompt, conversation=conversation, request=request
        )

    return helper


def _failed(error: str) -> dict[str, Any]:
    return {
        "helper": NAME,
        "status": ToolResultStatus.ERROR.value,
        "error": error,
        "hint": "Answer the owner with what you already have, or say you could not work it out.",
    }
