"""The tool port: what happens when a session calls a tool.

A session runs a loop and knows only that a tool call comes back with a result. What that
result *is* — a read over the `ai_*` views, an item on the screen, a helper's rows, a
prepared change — is answered here, out of what the composition root bound.

`ToolAdapters` is the runtime's `ToolRunner`: it says what each kind of session may call,
runs one call, and supplies the few sentences the loop has to say about tools.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime import (
    AgentDefinition,
    AgentSession,
    Observer,
    ToolOutcome,
    json_safe,
    log_preview,
)
from llm_gateway import ToolCall
from telegram_llm import DialogueMessage

from ..foundation.errors import failure_reason
from ..foundation.screens import ScreenCatalogue
from .contracts import (
    AgentChange,
    CallHelperInput,
    MutationToolSpec,
    OpenInput,
    QueryToolInput,
    RouteInput,
    ToolResultStatus,
    tool_json_schema,
    validation_error_summary,
)
from .conversation import conversation_block
from .mini import ReadToolSpec
from .sql import ReadOnlyQueryRunner, is_complex_read, read_query
from .subagents import RoutedSubagent

logger = logging.getLogger(__name__)

SUBAGENT_HISTORY_LAST_MESSAGES = 10

# What a helper is: it reads, it answers with rows, and it cannot open a screen. `route`
# is the other half — a subagent that writes, and whose screen suspends the whole chain.
Helper = Callable[..., Awaitable[dict[str, Any]]]


QUERY_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "query_data",
        "description": (
            "Read the current data with one read-only SELECT over the ai_* views listed "
            "in your instructions. Use it before you answer or propose anything."
        ),
        "parameters": tool_json_schema(QueryToolInput),
    },
}
OPEN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "open",
        "description": (
            "Put one item on the screen, exactly as the user opening it by hand. Call it only "
            "when the user asked to see or open one single item. Otherwise cite the item in "
            "your answer instead."
        ),
        "parameters": tool_json_schema(OpenInput),
    },
}
ROUTE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "route",
        "description": (
            "Hand this turn to a subagent. It reads this same conversation, does the work, "
            "and comes back with a receipt of what it did. You write the message the user "
            "sees. You just pass the name of the subagent."
        ),
        "parameters": tool_json_schema(RouteInput),
    },
}
CALL_HELPER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "call_helper",
        "description": (
            "Ask a helper a question one simple read could not answer. It writes the query "
            "and hands back its result. You keep the turn and you write the answer."
        ),
        "parameters": tool_json_schema(CallHelperInput),
    },
}
# The root session reads and routes. Every mutation tool belongs to the subagent that owns
# that feature, so judging *which* change to propose happens where the change is authored.
ROOT_SESSION_TOOLS = (QUERY_TOOL, OPEN_TOOL)
# The tools the adapters answer themselves, and the ones that run during the turn instead
# of becoming a proposal the owner approves. A session's own read tools are immediate too,
# but they are its own: a subagent that named one of these would never be heard.
IMMEDIATE_TOOLS = frozenset({"query_data", "route", "open", "call_helper"})


def query_read_tool(query_runner: ReadOnlyQueryRunner) -> ReadToolSpec:
    """`query_data` as a read tool a mini session declares for itself.

    A mini session never runs through `ToolAdapters`, so this is how it reaches the same
    door: the runner, its caps and its wording are `ai/sql.py`'s for every reader.
    """

    async def read(call: ToolCall) -> list[dict[str, Any]]:
        return (await read_query(query_runner, call)).rows

    return ReadToolSpec(QUERY_TOOL, read)


REPAIR_EXHAUSTED = (
    "I could not prepare the requested change after five repair attempts. "
    "No unfinished operation was applied."
)


def conversation_for(dialogue: list[dict[str, Any]]) -> str:
    """The tail of the conversation as data, for anyone who is not its assistant."""
    return conversation_block(
        [
            DialogueMessage(role=str(item["role"]), content=str(item["content"]))
            for item in dialogue[-SUBAGENT_HISTORY_LAST_MESSAGES:]
        ]
    )


def add_notice(rows: list[dict[str, Any]], text: str) -> None:
    """Attach a notice to a result, joining one that is already the last row.

    Two notice rows would be two instructions, and this model follows the last one it read.
    """
    if rows and set(rows[-1]) == {"notice"}:
        rows[-1] = {"notice": f"{rows[-1]['notice']} {text}"}
        return
    rows.append({"notice": text})


class MutationCatalogue(Protocol):
    """What the tool port needs of whoever turns a mutation call into a change.

    The engine dispatches the call and reports the failure; preparing the change and
    reviewing it belong to the mechanism the composition root binds in here.
    """

    tools: Mapping[str, MutationToolSpec]

    def change_from_tool(self, name: str, arguments: dict[str, Any]) -> AgentChange: ...


def mutation_repair_details(
    tool: MutationToolSpec | None, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Give the model a compact valid shape instead of a raw validator traceback."""
    if tool is None:
        return {}
    schema = tool_json_schema(tool.input_model)
    details: dict[str, Any] = {
        "expected_schema": {
            "required": schema.get("required", []),
            "allowed_properties": list(schema.get("properties", {})),
        }
    }
    if tool.repair is not None:
        details.update(tool.repair(arguments))
    return details


class ToolAdapters:
    """One method per tool the application answers, each taking a session and its call."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        query_runner: ReadOnlyQueryRunner,
        proposals: MutationCatalogue,
        trail: Observer,
        screens: ScreenCatalogue,
        helpers: Mapping[str, Helper] | None = None,
        subagents: Mapping[str, RoutedSubagent] | None = None,
    ) -> None:
        self.sessions = sessions
        self.query_runner = query_runner
        self.proposals = proposals
        # What `open` may put on the screen, and how its deep link is written.
        self.screens = screens
        # The same trail the runtime writes `route` to: one record of what a session did.
        self.trail = trail
        self.subagents = dict(subagents or {})
        # An empty roster means there is nothing to route to, so the tool is not offered.
        self.root_tools = (
            (*ROOT_SESSION_TOOLS, ROUTE_TOOL) if self.subagents else ROOT_SESSION_TOOLS
        )
        self.helpers = dict(helpers or {})
        # Built once: the offer is the whole of what the model is ever told about helpers,
        # so it has to name the tool in the shape the tool actually takes.
        self.helper_offer = (
            "This read is complex. call_helper("
            + " or ".join(f'"{name}"' for name in self.helpers)
            + ', "<your question in words>") writes the query and hands back its result.'
        )

    # ------------------------------------------------------------- the tool port

    def definition(self, kind: str) -> AgentDefinition:
        """What a session of this kind may call. The root session reads and routes; a
        subagent gets its own reads and the mutation tools of the features it owns.

        `query_data` is the one read door rather than any feature's read tool, so it is
        published here to every session. `IMMEDIATE_TOOLS` is the whole set the adapters
        answer themselves, and a subagent declares none of them.
        """
        routed = self.subagents.get(kind)
        if routed is None:
            return AgentDefinition(
                kind=kind, tools=self.root_tools, helper_tool=CALL_HELPER_TOOL
            )
        # No helper tool: a helper is offered by a complex read, and only the root
        # session's reads are ever offered one.
        return AgentDefinition(
            kind=kind,
            tools=(
                QUERY_TOOL,
                *(spec.schema for spec in routed.read_tools),
                *(self.proposals.tools[name].schema() for name in routed.mutation_tools),
            ),
            read_specs={spec.name: spec for spec in routed.read_tools},
        )

    def is_immediate(self, agent: AgentSession, name: str) -> bool:
        return name in IMMEDIATE_TOOLS or name in agent.read_specs

    async def run(self, agent: AgentSession, call: ToolCall) -> ToolOutcome:
        """Run one call. Anything that is not a read is a change waiting for the owner."""
        if call.name == "query_data":
            return ToolOutcome(result=await self.query(agent, call))
        if call.name == "open":
            return ToolOutcome(result=await self.open(agent, call))
        if call.name == "call_helper":
            return ToolOutcome(result=await self.call_helper(agent, call))
        if call.name in agent.read_specs:
            return ToolOutcome(result=await self.read(agent, call))
        change, result = await self.mutation(agent, call)
        return ToolOutcome(result=result, change=change)

    def route_target(self, call: ToolCall) -> tuple[str | None, dict[str, Any] | None]:
        try:
            name = RouteInput.model_validate(json.loads(call.arguments_json or "{}")).name.strip()
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as error:
            return None, {
                "status": ToolResultStatus.ERROR.value,
                "code": "invalid_arguments",
                "error": (
                    validation_error_summary(error)
                    if isinstance(error, ValidationError)
                    else str(error)
                ),
                "hint": f'Send {{"name": "<subagent>"}}. One of: {", ".join(self.subagents)}.',
                "retryable": True,
            }
        if name not in self.subagents:
            return None, {
                "status": ToolResultStatus.ERROR.value,
                "code": "unknown_subagent",
                "error": f"There is no subagent named {name!r}.",
                "hint": f"Route to one of: {', '.join(self.subagents) or 'none'}.",
                "retryable": True,
            }
        return name, None

    def refuse_mixed(self) -> dict[str, Any]:
        return {
            "status": ToolResultStatus.ERROR.value,
            "code": "mixed_read_and_mutation_tools",
            "error": "Mutation tools cannot share a response with a read tool or route.",
            "next": "Use the read result, then retry this mutation in the next response.",
            "retryable": True,
        }

    def prepared_message(self) -> str:
        return "I prepared the proposed changes for your approval."

    def repair_exhausted_message(self) -> str:
        return REPAIR_EXHAUSTED

    # ---------------------------------------------------------------- the tools

    async def call_helper(self, agent: AgentSession, call: ToolCall) -> dict[str, Any]:
        """Ask a helper one question and hand its rows back. Nothing suspends.

        The helper reads and answers with data, so this session keeps its turn: there is no
        screen to wait for and no receipt to compose.
        """
        try:
            payload = CallHelperInput.model_validate(json.loads(call.arguments_json or "{}"))
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as error:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "invalid_arguments",
                "error": (
                    validation_error_summary(error)
                    if isinstance(error, ValidationError)
                    else str(error)
                ),
                "hint": (
                    f'Send {{"name": "<helper>", "request": "<your question>"}}. '
                    f"One of: {', '.join(self.helpers)}."
                ),
                "retryable": True,
            }
        helper = self.helpers.get(payload.name.strip())
        if helper is None:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "unknown_helper",
                "error": f"There is no helper named {payload.name!r}.",
                "hint": f"Call one of: {', '.join(self.helpers) or 'none'}.",
                "retryable": True,
            }
        await self.trail.step(
            agent.run_id,
            agent.tool_count,
            "helper",
            {"tool_call_id": call.id, "helper": payload.name, "request": payload.request},
        )
        logger.info("HELPER -> %s %s", payload.name, log_preview(payload.request, 200))
        try:
            return await helper(
                conversation=conversation_for(agent.dialogue), request=payload.request
            )
        except Exception as error:
            # A helper is an optimisation. Losing the turn because one broke would be worse
            # than the answer the Advisor can still give from what it read itself.
            logger.exception("Helper %s failed", payload.name)
            return {
                "helper": payload.name,
                "status": ToolResultStatus.ERROR.value,
                "error": failure_reason(error),
                "hint": "Answer the owner with what you already have.",
            }

    def _should_offer_helper(
        self, agent: AgentSession, sql: str, rows: list[dict[str, Any]]
    ) -> bool:
        """Whether this read earned the model a helper it was not already carrying.

        A read that failed does not: its `hint` already says to repair that one SELECT, and
        a second instruction in the same result is the one this model would follow.
        """
        if not self.helpers or self.subagents.get(agent.kind) is not None or not sql:
            return False
        if rows and rows[0].get("status") == ToolResultStatus.ERROR:
            return False
        capped = bool(rows) and set(rows[-1]) == {"notice"}
        return capped or is_complex_read(sql)

    async def read(self, agent: AgentSession, call: ToolCall) -> Any:
        """Run one of this session's own read tools and record that it ran."""
        result = await agent.read_specs[call.name].run(call)
        await self.trail.step(
            agent.run_id,
            agent.tool_count,
            "read",
            {
                "tool_call_id": call.id,
                "tool": call.name,
                "arguments": call.arguments_json,
            },
        )
        logger.info("AI TOOL %s(%s)", call.name, log_preview(call.arguments_json, 200))
        return result

    async def query(self, agent: AgentSession, call: ToolCall) -> list[dict[str, Any]]:
        """The one read door, for every session the adapters run.

        The read itself is `ai/sql.py`'s. What is here is the session's half of it: the
        helper a complex read earns, and the trail that keeps the SQL a local model wrote
        beside the rows it got back.
        """
        read = await read_query(self.query_runner, call)
        sql, rows = read.sql, read.rows
        if self._should_offer_helper(agent, sql, rows):
            agent.offer_helper()
            add_notice(rows, self.helper_offer)
        logger.info(
            "AI TOOL query_data -> rows=%d sql=%s",
            len(rows),
            log_preview(sql, 700),
        )
        await self.trail.step(
            agent.run_id,
            agent.tool_count,
            "read_query",
            {
                "tool_call_id": call.id,
                "tool": call.name,
                "arguments": call.arguments_json,
                "sql": sql,
                "row_count": len(rows),
                "columns": list(rows[0]) if rows else [],
                "result": json_safe(rows),
            },
        )
        return rows

    async def open(self, agent: AgentSession, call: ToolCall) -> dict[str, Any]:
        """Resolve the item to show and hand it to the session that writes to the chat."""
        try:
            request = OpenInput.model_validate(json.loads(call.arguments_json or "{}"))
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as error:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "invalid_arguments",
                "error": (
                    validation_error_summary(error)
                    if isinstance(error, ValidationError)
                    else str(error)
                ),
                "hint": 'Send {"item_type": "card", "id": 12}.',
                "retryable": True,
            }
        async with self.sessions() as session:
            item = await session.get(self.screens.by_type[request.item_type].model, request.id)
            if item is None:
                return {
                    "status": ToolResultStatus.ERROR.value,
                    "code": "not_found",
                    "error": f"There is no {request.item_type} #{request.id}.",
                    "hint": "Find the id with query_data, then call open again.",
                    "retryable": True,
                }
            item_id = item.id
        agent.host_state["open_item"] = self.screens.payload(request.item_type, item_id)
        logger.info("AI TOOL open -> %s", agent.host_state["open_item"])
        return {
            "status": ToolResultStatus.OK.value,
            "opened": {"item_type": request.item_type, "id": item_id},
            "next": "The screen follows your message. Answer in one short line.",
        }

    async def mutation(
        self, agent: AgentSession, call: ToolCall
    ) -> tuple[AgentChange | None, dict[str, Any]]:
        arguments: Any = None
        try:
            arguments = json.loads(call.arguments_json)
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            change = self.proposals.change_from_tool(call.name, arguments)
        except (ValueError, ValidationError, json.JSONDecodeError) as error:
            logger.info("AI TOOL %s rejected: %s", call.name, error)
            error_text = (
                validation_error_summary(error)
                if isinstance(error, ValidationError)
                else str(error)
            )
            result = {
                "status": ToolResultStatus.ERROR.value,
                "code": "invalid_arguments",
                "error": error_text,
                "hint": (
                    "Retry only this unfinished tool call using expected_arguments and the "
                    "argument_rules below; do not repeat successful calls."
                ),
                "retryable": True,
            }
            if isinstance(arguments, dict):
                result.update(
                    mutation_repair_details(self.proposals.tools.get(call.name), arguments)
                )
            return None, result
        logger.info("AI TOOL %s prepared %s.%s", call.name, change.entity, change.action)
        await self.trail.step(
            agent.run_id,
            agent.tool_count,
            "mutation_intent",
            {
                "tool_call_id": call.id,
                "arguments": call.arguments_json,
                "tool": call.name,
                "entity": change.entity,
                "action": change.action,
                "id": change.id,
            },
        )
        return change, {
            "status": ToolResultStatus.PREPARED.value,
            "entity": change.entity,
            "action": change.action,
            "id": change.id,
            "next": "Wait for the user's review or approval; do not say it is complete.",
        }
