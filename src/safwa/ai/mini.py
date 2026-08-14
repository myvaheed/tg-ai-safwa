"""One-question model sessions that end in a single terminal tool call.

Deliberately not ``_run_agent_loop``: that loop is entangled with proposals, ``AgentRun``
rows and the approval queue, none of which a mini-session has.  A mini-session gets its own
system prompt, a small context, at most one read tool, and a set of terminal tools of which
exactly one must be called — the call *is* the answer, so prose is never accepted.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ..constants import MINI_SESSION_REPAIR_ROUNDS
from .contracts import tool_json_schema
from .provider import OpenAICompatibleProvider, ProviderToolCall

logger = logging.getLogger(__name__)

ReadTool = Callable[[ProviderToolCall], Awaitable[list[dict[str, Any]]]]


class MiniSessionError(RuntimeError):
    """The session never produced a usable terminal call."""


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
    provider: OpenAICompatibleProvider,
    *,
    system_prompt: str,
    context: str,
    terminals: tuple[TerminalTool, ...],
    read_tool: tuple[dict[str, Any], ReadTool] | None = None,
    max_tool_calls: int,
    max_repairs: int = MINI_SESSION_REPAIR_ROUNDS,
) -> MiniSessionResult:
    """Run until one terminal tool validates, or give up and say why.

    Anything that is not a terminal call — prose, an unknown tool, arguments that fail
    validation — is fed back as a retryable tool result, the same shape the main loop uses,
    so the model repairs the call instead of the caller guessing what it meant.
    """
    by_name = {terminal.name: terminal for terminal in terminals}
    tools = [terminal.schema() for terminal in terminals]
    if read_tool is not None:
        tools.insert(0, read_tool[0])
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ]
    calls = 0
    repairs = 0
    while True:
        turn = await provider.complete_turn(messages, tools=tools)
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
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in turn.tool_calls
                ],
            }
        )
        for call in turn.tool_calls:
            calls += 1
            if calls > max_tool_calls:
                raise MiniSessionError("The session exceeded its tool-call budget")
            if read_tool is not None and call.name == read_tool[0]["function"]["name"]:
                rows = await read_tool[1](call)
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
                payload = terminal.model.model_validate(json.loads(call.arguments or "{}"))
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


def _reply(messages: list[dict[str, Any]], call: ProviderToolCall, content: Any) -> None:
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(content, ensure_ascii=False, default=str),
        }
    )
