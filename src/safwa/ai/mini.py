"""Model sessions that end in a single terminal tool call.

A session gets its own system prompt, its own context, its own read tools, and a set of
terminal tools of which exactly one must be called — the call *is* the answer, so prose is
never accepted.  It touches no proposal and no approval queue, and it cannot continue after
its one answer: anything that has to suspend and resume is a session in `service.py`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from llm_gateway import CompletionRequest, LlmProvider, ToolCall

from ..constants import MINI_SESSION_REPAIR_ROUNDS
from .contracts import QueryToolInput, tool_json_schema
from .sql import ReadOnlyQueryRunner, UnsafeQueryError

logger = logging.getLogger(__name__)

ReadTool = Callable[[ToolCall], Awaitable[Any]]


class MiniSessionError(RuntimeError):
    """The session never produced a usable terminal call."""


@dataclass(frozen=True)
class ReadToolSpec:
    """A tool the session may call as often as it likes without ending."""

    schema: dict[str, Any]
    run: ReadTool

    @property
    def name(self) -> str:
        return str(self.schema["function"]["name"])


@dataclass(frozen=True)
class TerminalTool:
    """One acceptable ending, and the model that validates its arguments."""

    name: str
    description: str
    model: type[BaseModel]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": tool_json_schema(self.model),
            },
        }


@dataclass(frozen=True)
class MiniSessionResult:
    name: str
    payload: BaseModel


async def run_mini_session(
    provider: LlmProvider,
    *,
    system_prompt: str,
    context: str,
    terminals: tuple[TerminalTool, ...],
    read_tools: tuple[ReadToolSpec, ...] = (),
    max_tool_calls: int | None,
    max_repairs: int = MINI_SESSION_REPAIR_ROUNDS,
) -> MiniSessionResult:
    """Run until one terminal tool validates, or give up and say why.

    Anything that is not a terminal call — prose, an unknown tool, arguments that fail
    validation — is fed back as a retryable tool result, the same shape the main loop uses,
    so the model repairs the call instead of the caller guessing what it meant.
    """
    by_name = {terminal.name: terminal for terminal in terminals}
    readers = {spec.name: spec for spec in read_tools}
    tools = [spec.schema for spec in read_tools] + [
        terminal.schema() for terminal in terminals
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ]
    calls = 0
    repairs = 0
    while True:
        turn = await provider.complete(
            CompletionRequest(messages=tuple(messages), tools=tuple(tools))
        )
        if not turn.tool_calls:
            repairs += 1
            if repairs > max_repairs:
                raise MiniSessionError(
                    f"The session answered in prose instead of calling one of: "
                    f"{', '.join(by_name)}"
                )
            messages.append({"role": "assistant", "content": turn.content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Do not answer in prose. Call exactly one of: "
                        f"{', '.join(by_name)}."
                    ),
                }
            )
            continue

        messages.append(
            {
                "role": "assistant",
                "content": turn.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments_json},
                    }
                    for call in turn.tool_calls
                ],
            }
        )
        for call in turn.tool_calls:
            calls += 1
            if max_tool_calls is not None and calls > max_tool_calls:
                raise MiniSessionError("The session exceeded its tool-call budget")
            reader = readers.get(call.name)
            if reader is not None:
                rows = await reader.run(call)
                _reply(messages, call, rows)
                continue
            terminal = by_name.get(call.name)
            if terminal is None:
                repairs += 1
                _reply(
                    messages,
                    call,
                    {
                        "status": "error",
                        "code": "unknown_tool",
                        "error": f"Unknown tool: {call.name}",
                        "hint": f"Call exactly one of: {', '.join(by_name)}.",
                        "retryable": True,
                    },
                )
                continue
            try:
                payload = terminal.model.model_validate(json.loads(call.arguments_json or "{}"))
            except (ValidationError, json.JSONDecodeError, TypeError) as error:
                repairs += 1
                _reply(
                    messages,
                    call,
                    {
                        "status": "error",
                        "code": "invalid_arguments",
                        "error": str(error),
                        "hint": f"Fix the arguments and call {call.name} again.",
                        "retryable": True,
                    },
                )
                continue
            logger.info("MINI SESSION -> %s %s", call.name, payload)
            return MiniSessionResult(name=call.name, payload=payload)
        if repairs > max_repairs:
            raise MiniSessionError(
                f"The session could not produce a valid call to one of: {', '.join(by_name)}"
            )


def _reply(messages: list[dict[str, Any]], call: ToolCall, content: Any) -> None:
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(content, ensure_ascii=False, default=str),
        }
    )


QUERY_SAFWA_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "query_safwa",
        "description": (
            "Read Safwa's current data with one read-only SELECT over the ai_* views listed "
            "in your instructions. Use it before you answer or propose anything."
        ),
        "parameters": tool_json_schema(QueryToolInput),
    },
}


def query_read_tool(query_runner: ReadOnlyQueryRunner) -> ReadToolSpec:
    """`query_safwa` as a plain read tool, for a session that declares its own tools.

    The runner and its caps are shared; the session executes it through the same path
    the Advisor uses, so its steps are recorded the same way.
    """

    async def read(call: ToolCall) -> list[dict[str, Any]]:
        try:
            query = QueryToolInput.model_validate(json.loads(call.arguments_json or "{}"))
            outcome = await query_runner.run(query.sql)
            return outcome.as_tool_result()
        except (
            UnsafeQueryError,
            sqlite3.Error,
            TimeoutError,
            OSError,
            ValidationError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
            return [
                {
                    "status": "error",
                    "code": "query_failed",
                    "error": str(error),
                    "hint": (
                        "Fix only this SELECT and call query_safwa again. One read-only "
                        "SELECT or WITH … SELECT over the ai_* views."
                    ),
                    "retryable": True,
                }
            ]

    return ReadToolSpec(QUERY_SAFWA_TOOL, read)
