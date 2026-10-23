"""One session, run until it answers in words.

The loop knows five endings and no others: the model wrote something or said it had
nothing to do, it forwarded the words a session it routed to handed back, its calls became
changes that a person has to see, a session it routed to opened a screen and the whole
chain now waits, or it called one of the application's tools that end a session.
Everything else — what a tool does, what a change is, what an ending means — is a port.
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
    answered_calls,
    call_key,
    flatten_content,
    log_preview,
    routed_answers,
    tool_message,
)
from .ports import Observer, ToolRunner
from .tracing import record_output, tool_span

logger = logging.getLogger(__name__)

RouteHandler = Callable[[AgentSession, ToolCall], Awaitable[tuple[dict[str, Any], TurnOutcome | None]]]

# The runtime's own tools, answered here and never by the `ToolRunner`. Each ends a step on
# its own: a route may suspend the chain, and a forward or a nothing_to_do ends the turn.
_NOT_SHARED = {
    "route": {
        "status": "error",
        "code": "route_is_not_shared",
        "error": "route must be the only tool call in a response.",
        "next": "Send route alone, then use what it hands back.",
        "retryable": True,
    },
    "forward": {
        "status": "error",
        "code": "forward_is_not_shared",
        "error": "forward must be the only tool call in a response.",
        "next": "Send forward alone: it ends your turn.",
        "retryable": True,
    },
    "nothing_to_do": {
        "status": "error",
        "code": "nothing_to_do_is_not_shared",
        "error": "nothing_to_do must be the only tool call in a response.",
        "next": "Send nothing_to_do alone: it ends your turn.",
        "retryable": True,
    },
}
RUNTIME_TOOLS = frozenset(_NOT_SHARED)


def _not_shared(name: str) -> dict[str, Any]:
    """What every call of a response is told when it shares the response with `name`.

    An ending tool of the application's is refused in the words `forward` is.
    """
    return _NOT_SHARED.get(name) or {
        "status": "error",
        "code": f"{name}_is_not_shared",
        "error": f"{name} must be the only tool call in a response.",
        "next": f"Send {name} alone: it ends your turn.",
        "retryable": True,
    }

# A read that gives back what the same read gave earlier in this session. Asked again, a
# small model reads the same rows and sends the same call: nothing it reads ever changes.
_REPEATED_CALL = {
    "status": "error",
    "code": "repeated_call",
    "error": "You made this same call before, and it returned the same result.",
    "next": "Use that earlier result. Answer now, or make a different call.",
    "retryable": False,
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


def _forward(
    agent: AgentSession, call: ToolCall
) -> tuple[str, str | None, dict[str, Any]]:
    """The newest words the named subagent handed back in this request, to send as they
    are — or, with no words, the result that says what can be sent instead."""
    answers = routed_answers(agent.transcript)
    try:
        name = str(json.loads(call.arguments_json or "{}").get("name") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        name = ""
    if name in answers:
        return name, answers[name][1], {"status": "ok", "forwarded": name}
    return name, None, {
        "status": "error",
        "code": "nothing_to_forward",
        "error": f"No subagent named {name!r} answered in this request.",
        "next": (
            f"Call forward with one of: {', '.join(answers)}."
            if answers
            else "Nothing to forward. Answer in your own words."
        ),
        "retryable": True,
    }


def _nothing_to_do(call: ToolCall) -> tuple[str, dict[str, Any]]:
    """The reason the session gives for ending with no work, or the result asking for one."""
    try:
        reason = str(json.loads(call.arguments_json or "{}").get("reason") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        reason = ""
    if reason:
        return reason, {"status": "ok"}
    return "", {
        "status": "error",
        "code": "reason_missing",
        "error": "nothing_to_do needs a reason.",
        "next": "Call nothing_to_do again with the reason in one short sentence.",
        "retryable": True,
    }


class ToolBudgetExceeded(RuntimeError):
    """The session made more tool calls than one turn is allowed."""


async def provider_turn(agent: AgentSession, provider: LlmProvider) -> CompletionTurn:
    # Only the first move is forced: the loop ends on a turn that calls no tool, and a
    # session that must always call one never ends.
    required = agent.first_call_required and agent.tool_count == 0
    log_provider_request(agent.messages)
    turn = await provider.complete(
        CompletionRequest(
            messages=tuple(agent.messages),
            tools=tuple(agent.tools),
            tool_choice="required" if required else None,
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
    max_tool_calls: int,
    max_repair_rounds: int,
    observer: Observer | None = None,
) -> AgentLoopResult:
    """Run the model until it answers in words.

    Stopping with no content is not an answer, so the session is told so and runs on, and
    a read that gives back what the same read gave before is told it learned nothing new.
    Repair rounds bound both, and an empty result after them is the `Materializer`'s to
    turn into something a person can read.
    """
    messages = agent.messages
    while True:
        if observer is not None:
            await observer.asking(agent)
        turn = await provider_turn(agent, provider)
        if turn.tool_calls:
            messages.append(turn.as_message())
            # A route can suspend the whole chain, and a suspended response cannot carry
            # results for its siblings: the transcript would resume malformed. A forward
            # or an ending tool ends the turn, which its siblings would not survive either.
            alone = RUNTIME_TOOLS | agent.ending_tools
            not_shared = (
                next((call.name for call in turn.tool_calls if call.name in alone), None)
                if len(turn.tool_calls) > 1
                else None
            )
            pending_tools: list[PendingTool] = []
            available = agent.tool_names
            immediate = {
                call.name: call.name in alone or tools.is_immediate(agent, call.name)
                for call in turn.tool_calls
                if call.name in available
            }
            has_reads = any(immediate.values())
            has_mutations = not all(immediate.values())
            repeated = False
            for call in turn.tool_calls:
                # Every call is charged, refused ones included: a response the loop answers
                # without running anything is still a response, and a session that only ever
                # sends malformed ones would otherwise never reach its limit.
                agent.tool_count += 1
                if agent.tool_count > max_tool_calls:
                    raise ToolBudgetExceeded("The session exceeded the tool-call limit")
                with tool_span(call) as span:
                    change = None
                    if not_shared is not None:
                        result = _not_shared(not_shared)
                    elif call.name not in available:
                        result = _tool_not_available(agent, call.name)
                    elif call.name == "route":
                        result, suspended = await route(agent, call)
                        if suspended is not None:
                            record_output(span, result)
                            return AgentLoopResult(message="", suspended=suspended)
                    elif call.name == "forward":
                        name, words, result = _forward(agent, call)
                        if words is not None:
                            record_output(span, result)
                            messages.append(tool_message(call.id, call.name, result))
                            if observer is not None:
                                await observer.step(
                                    agent.run_id,
                                    agent.tool_count,
                                    "forward",
                                    {"tool_call_id": call.id, "subagent": name},
                                )
                            logger.info("FORWARD <- %s", name)
                            return AgentLoopResult(words, forwarded=name)
                    elif call.name == "nothing_to_do":
                        reason, result = _nothing_to_do(call)
                        if reason:
                            record_output(span, result)
                            messages.append(tool_message(call.id, call.name, result))
                            logger.info("NOTHING TO DO: %s", log_preview(reason))
                            return AgentLoopResult(reason)
                    elif call.name in agent.ending_tools:
                        # A call the runner refuses is answered like any other, and the
                        # session runs on to correct it.
                        outcome = await tools.run(agent, call)
                        result = outcome.result
                        if outcome.succeeded:
                            record_output(span, result)
                            messages.append(tool_message(call.id, call.name, result))
                            logger.info("ENDED BY %s", call.name)
                            return AgentLoopResult(
                                "", ended=PendingTool(call=call, result=result)
                            )
                    elif immediate[call.name]:
                        result = (await tools.run(agent, call)).result
                        answered = (
                            call_key(call.name, call.arguments_json),
                            tool_message(call.id, call.name, result)["content"],
                        )
                        if answered in answered_calls(messages):
                            result, repeated = _REPEATED_CALL, True
                    elif has_reads and has_mutations:
                        result = tools.refuse_mixed()
                    else:
                        outcome = await tools.run(agent, call)
                        change, result = outcome.change, outcome.result
                    record_output(span, result)
                pending_tools.append(PendingTool(call=call, result=result, change=change))
                messages.append(tool_message(call.id, call.name, result))
            if not_shared is not None:
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
            if invalid_mutations or repeated:
                if agent.repair_rounds >= max_repair_rounds:
                    return AgentLoopResult(
                        message=tools.repair_exhausted_message() if invalid_mutations else ""
                    )
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
