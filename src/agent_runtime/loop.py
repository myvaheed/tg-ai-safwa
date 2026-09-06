"""One session, run until it answers in words.

The loop knows three endings and no others: the model wrote something, its calls became
changes that a person has to see, or a session it routed to opened a screen and the whole
chain now waits. Everything else — what a tool does, what a change is — is a port.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from llm_gateway import CompletionRequest, CompletionTurn, LlmProvider, ToolCall

from .context import system_note
from .model import (
    AgentLoopResult,
    AgentSession,
    PendingTool,
    TurnOutcome,
    flatten_content,
    log_preview,
)
from .ports import ToolRunner

logger = logging.getLogger(__name__)

RouteHandler = Callable[[AgentSession, ToolCall], Awaitable[tuple[dict[str, Any], TurnOutcome | None]]]

_ROUTE_IS_NOT_SHARED = {
    "status": "error",
    "code": "route_is_not_shared",
    "error": "route must be the only tool call in a response.",
    "next": "Send route alone, then use what it hands back.",
    "retryable": True,
}

_STOPPED_WITHOUT_ANSWERING = (
    "You stopped without answering. Write the answer to the owner now, "
    "in their language, using what the tool results already gave you."
)


def _tool_not_available(agent: AgentSession, name: str) -> dict[str, Any]:
    """What a call is told when this session was never handed that tool."""
    return {
        "status": "error",
        "code": "tool_not_available",
        "error": f"You have no tool named {name!r}.",
        "next": f"Call one of: {', '.join(sorted(agent.tool_names))}.",
        "retryable": True,
    }


class ToolBudgetExceeded(RuntimeError):
    """The session made more tool calls than one turn is allowed."""


async def provider_turn(
    agent: AgentSession,
    provider: LlmProvider,
    *,
    first_call_required: bool = False,
) -> CompletionTurn:
    log_provider_request(agent.messages)
    turn = await provider.complete(
        CompletionRequest(
            messages=tuple(agent.messages),
            tools=tuple(agent.tools),
            tool_choice="required" if first_call_required and agent.tool_count == 0 else None,
        )
    )
    log_provider_response(turn)
    return turn


async def run_loop(
    agent: AgentSession,
    *,
    provider: LlmProvider,
    tools: ToolRunner,
    route: RouteHandler,
    routed_kinds: frozenset[str],
    max_tool_calls: int,
    max_repair_rounds: int,
) -> AgentLoopResult:
    """Run the model until it answers in words.

    Stopping with no content is not an answer, so the session is told so and runs on.
    Repair rounds bound that, and an empty result after them is the `Materializer`'s to
    turn into something a person can read.
    """
    messages = agent.messages
    while True:
        # A subagent was routed to for the work, so its first move is the work. Only the
        # first: the loop ends on a turn that calls no tool, and a session that must always
        # call one never ends.
        turn = await provider_turn(
            agent, provider, first_call_required=agent.kind in routed_kinds
        )
        if turn.tool_calls:
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
            # A route can suspend the whole chain, and a suspended response cannot carry
            # results for its siblings: the transcript would resume malformed.
            route_not_shared = len(turn.tool_calls) > 1 and any(
                call.name == "route" for call in turn.tool_calls
            )
            pending_tools: list[PendingTool] = []
            available = agent.tool_names
            immediate = {
                call.name: tools.is_immediate(agent, call.name)
                for call in turn.tool_calls
                if call.name in available
            }
            has_reads = any(immediate.values())
            has_mutations = not all(immediate.values())
            for call in turn.tool_calls:
                # Every call is charged, refused ones included: a response the loop answers
                # without running anything is still a response, and a session that only ever
                # sends malformed ones would otherwise never reach its limit.
                agent.tool_count += 1
                if agent.tool_count > max_tool_calls:
                    raise ToolBudgetExceeded("The session exceeded the tool-call limit")
                change = None
                if route_not_shared:
                    result = _ROUTE_IS_NOT_SHARED
                elif call.name not in available:
                    result = _tool_not_available(agent, call.name)
                elif call.name == "route":
                    result, suspended = await route(agent, call)
                    if suspended is not None:
                        return AgentLoopResult(message="", suspended=suspended)
                elif immediate[call.name]:
                    result = (await tools.run(agent, call)).result
                elif has_reads and has_mutations:
                    result = tools.refuse_mixed()
                else:
                    outcome = await tools.run(agent, call)
                    change, result = outcome.change, outcome.result
                pending_tools.append(PendingTool(call=call, result=result, change=change))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
            if route_not_shared:
                continue
            changes = [tool.change for tool in pending_tools if tool.change is not None]
            if changes:
                return AgentLoopResult(
                    message=tools.prepared_message(),
                    pending_tools=pending_tools,
                )
            invalid_mutations = [
                tool
                for tool in pending_tools
                if not immediate.get(tool.call.name, True) and tool.change is None
            ]
            if invalid_mutations:
                if agent.repair_rounds >= max_repair_rounds:
                    return AgentLoopResult(message=tools.repair_exhausted_message())
                agent.repair_rounds += 1
            continue

        if turn.content:
            return AgentLoopResult(turn.content)
        if agent.repair_rounds >= max_repair_rounds:
            return AgentLoopResult("")
        agent.repair_rounds += 1
        # A turn that stops with nothing leaves the owner with nothing.  The empty
        # assistant message is dropped rather than kept: it carries no information and
        # some chat templates reject it.
        messages.append(system_note(_STOPPED_WITHOUT_ANSWERING))


def log_provider_request(messages: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    for message in messages:
        content = flatten_content(message.get("content"))
        if message.get("tool_calls"):
            content = "tool calls: " + ", ".join(
                call["function"]["name"] for call in message["tool_calls"]
            )
        elif message.get("role") == "tool":
            content = f"{message.get('name')}: {content}"
        lines.append(f"  {message['role']:<9} {log_preview(content)}")
    logger.info("AI REQUEST ->\n%s\n%s", "\n".join(lines), "-" * 72)


def log_provider_response(turn: CompletionTurn) -> None:
    if turn.tool_calls:
        details = "\n".join(
            f"  tool {call.name}({log_preview(call.arguments_json, 700)})"
            for call in turn.tool_calls
        )
    else:
        details = "  " + log_preview(turn.content, 1_000)
    if turn.usage is not None:
        usage = turn.usage
        cost = "" if usage.cost is None else f" cost={usage.cost}"
        details += (
            f"\n  usage prompt={usage.prompt_tokens} cached={usage.cached_tokens} "
            f"cache_write={usage.cache_write_tokens} completion={usage.completion_tokens}{cost}"
        )
    logger.info("AI RESPONSE <-\n%s\n%s", details, "-" * 72)
