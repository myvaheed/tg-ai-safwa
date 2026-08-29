"""Safwa's side of the tool port: what the application does when a session calls a tool.

A session runs a loop and knows only that a tool call comes back with a result. What that
result *is* — a read over the `ai_*` views, an item on the screen, a helper's rows, a
prepared change — is Safwa's, and none of it can live in a package that must work without
Safwa. So the loop asks, and this module answers.

`ToolSession` is the whole of what an adapter may touch on the session that called it.
Naming it here rather than importing the session keeps the dependency pointing one way.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import ToolCall

from ..constants import SUBAGENT_HISTORY_LAST_MESSAGES
from ..features.diary.model import DiaryEntry
from ..features.proposals.api import MutationToolSpec, ProposalRegistry
from ..foundation.errors import failure_reason
from ..history import citation_payload, conversation_block
from ..models import AgentStep, Card, Check, SavedRequest, Tag, Value
from .context import DialogueMessage
from .contracts import (
    AgentChange,
    CallHelperInput,
    OpenInput,
    QueryToolInput,
    ToolResultStatus,
    tool_json_schema,
)
from .mini import ReadToolSpec
from .sql import ReadOnlyQueryRunner, UnsafeQueryError, is_complex_read

logger = logging.getLogger(__name__)

# The `open` tool's targets.  This is the last central item-kind table left in the AI layer;
# `telegram/screens.py` holds the other half of it, and the two fold into one screen
# registry when the Telegram adapters move into their features.
OPENABLE_MODELS: dict[str, Any] = {
    "card": Card,
    "check": Check,
    "tag": Tag,
    "value": Value,
    "request": SavedRequest,
    "diary": DiaryEntry,
}

# What a helper is: it reads, it answers with rows, and it cannot open a screen. `route`
# is the other half — a subagent that writes, and whose screen suspends the whole chain.
Helper = Callable[..., Awaitable[dict[str, Any]]]


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def log_preview(content: str, limit: int = 500) -> str:
    compact = " ".join(content.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


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


def validation_error_summary(error: ValidationError) -> str:
    messages: list[str] = []
    for issue in error.errors(include_url=False, include_input=False):
        location = ".".join(str(item) for item in issue.get("loc", ()))
        message = str(issue.get("msg", "Invalid value"))
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages) or "Invalid tool arguments"


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


class ToolSession(Protocol):
    """What a tool call may read and write on the session that made it."""

    run_id: int
    tool_count: int
    kind: str
    dialogue: list[dict[str, Any]]
    read_specs: dict[str, ReadToolSpec]
    open_item: str | None

    def offer_helper(self) -> None:
        """Put `call_helper` on this session's tools, once."""


class ToolAdapters:
    """One method per tool Safwa answers. Each takes the calling session and its call."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        query_runner: ReadOnlyQueryRunner,
        proposals: ProposalRegistry,
        helpers: Mapping[str, Helper] | None = None,
    ) -> None:
        self.sessions = sessions
        self.query_runner = query_runner
        self.proposals = proposals
        self.helpers = dict(helpers or {})
        # Built once: the offer is the whole of what the model is ever told about helpers,
        # so it has to name the tool in the shape the tool actually takes.
        self.helper_offer = (
            "This read is complex. call_helper("
            + " or ".join(f'"{name}"' for name in self.helpers)
            + ', "<your question in words>") writes the query and hands back its result.'
        )

    async def call_helper(self, agent: ToolSession, call: ToolCall) -> dict[str, Any]:
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
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="helper",
                    metadata_json={
                        "tool_call_id": call.id,
                        "helper": payload.name,
                        "request": payload.request,
                    },
                )
            )
            await session.commit()
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
        self, agent: ToolSession, sql: str, rows: list[dict[str, Any]]
    ) -> bool:
        """Whether this read earned the model a helper it was not already carrying.

        A read that failed does not: its `hint` already says to repair that one SELECT, and
        a second instruction in the same result is the one this model would follow.
        """
        if not self.helpers or agent.kind != "advisor" or not sql:
            return False
        if rows and rows[0].get("status") == ToolResultStatus.ERROR:
            return False
        capped = bool(rows) and set(rows[-1]) == {"notice"}
        return capped or is_complex_read(sql)

    async def read(self, agent: ToolSession, call: ToolCall) -> Any:
        """Run one of this session's own read tools and record that it ran."""
        result = await agent.read_specs[call.name].run(call)
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="read",
                    metadata_json={
                        "tool_call_id": call.id,
                        "tool": call.name,
                        "arguments": call.arguments_json,
                    },
                )
            )
            await session.commit()
        logger.info("AI TOOL %s(%s)", call.name, log_preview(call.arguments_json, 200))
        return result

    async def query(self, agent: ToolSession, call: ToolCall) -> list[dict[str, Any]]:
        if call.name != "query_safwa":
            rows: list[dict[str, Any]] = [
                {
                    "status": ToolResultStatus.ERROR.value,
                    "code": "unknown_tool",
                    "error": f"Unknown tool: {call.name}",
                    "hint": (
                        "Call one of: query_safwa, card, check, value, tag, request, reminder, "
                        "remove."
                    ),
                    "retryable": True,
                }
            ]
            sql = ""
        else:
            try:
                arguments = json.loads(call.arguments_json)
                query = QueryToolInput.model_validate(arguments)
                sql = query.sql
                outcome = await self.query_runner.run(sql)
                rows = outcome.as_tool_result()
                if outcome.notice:
                    logger.info("AI TOOL query_safwa capped: %s", outcome.notice)
            # ``UnsafeQueryError`` is a ``ValueError``, so it has to be caught before the
            # argument-shape clause or a rejected SELECT is reported as a bad argument and
            # the model rewrites the call instead of the query.
            except (UnsafeQueryError, sqlite3.Error, TimeoutError, OSError) as error:
                # A rejected or broken read is the model's to repair. Raising here would
                # end the whole request, including any mutation queued alongside it.
                rows = [
                    {
                        "status": ToolResultStatus.ERROR.value,
                        "code": "unsafe_query"
                        if isinstance(error, UnsafeQueryError)
                        else "query_failed",
                        "error": str(error),
                        "hint": (
                            "Fix only this SELECT and call query_safwa again. One read-only "
                            "SELECT or WITH … SELECT over the ai_* views, no other statement. "
                            "This failure changed nothing: every step of the request already "
                            "resolved above still stands, so do not restart the request."
                        ),
                        "retryable": True,
                    }
                ]
            except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
                sql = ""
                rows = [
                    {
                        "status": ToolResultStatus.ERROR.value,
                        "code": "invalid_arguments",
                        "error": (
                            validation_error_summary(error)
                            if isinstance(error, ValidationError)
                            else str(error)
                        ),
                        "hint": (
                            'Send exactly one string argument, e.g. {"sql": "SELECT id, title '
                            'FROM ai_cards LIMIT 20"}, and call query_safwa again.'
                        ),
                        "retryable": True,
                    }
                ]
        if self._should_offer_helper(agent, sql, rows):
            agent.offer_helper()
            add_notice(rows, self.helper_offer)
        logger.info(
            "AI TOOL query_safwa -> rows=%d sql=%s",
            len(rows),
            log_preview(sql, 700),
        )
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="read_query",
                    metadata_json={
                        "tool_call_id": call.id,
                        "tool": call.name,
                        "arguments": call.arguments_json,
                        "sql": sql,
                        "row_count": len(rows),
                        "columns": list(rows[0]) if rows else [],
                        "result": json_safe(rows),
                    },
                )
            )
            await session.commit()
        return rows

    async def open(self, agent: ToolSession, call: ToolCall) -> dict[str, Any]:
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
            item = await session.get(OPENABLE_MODELS[request.item_type], request.id)
            if item is None:
                return {
                    "status": ToolResultStatus.ERROR.value,
                    "code": "not_found",
                    "error": f"There is no {request.item_type} #{request.id}.",
                    "hint": "Find the id with query_safwa, then call open again.",
                    "retryable": True,
                }
            item_id = item.id
        agent.open_item = citation_payload(request.item_type, item_id)
        logger.info("AI TOOL open -> %s", agent.open_item)
        return {
            "status": ToolResultStatus.OK.value,
            "opened": {"item_type": request.item_type, "id": item_id},
            "next": "The screen follows your message. Answer in one short line.",
        }

    async def mutation(
        self, agent: ToolSession, call: ToolCall
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
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="mutation_intent",
                    metadata_json={
                        "tool_call_id": call.id,
                        "arguments": call.arguments_json,
                        "tool": call.name,
                        "entity": change.entity,
                        "action": change.action,
                        "id": change.id,
                    },
                )
            )
            await session.commit()
        return change, {
            "status": ToolResultStatus.PREPARED.value,
            "entity": change.entity,
            "action": change.action,
            "id": change.id,
            "next": "Wait for the user's review or approval; do not say it is complete.",
        }
