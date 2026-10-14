"""Clarify a new Value only when it does not meet the Value criteria.

The hook's work checks each new Value with one small model call, which answers clear or
unclear with its advice, and keeps the unclear one with that advice as its pending request;
every pending Value is worded in one request, which waits behind the Cue gate like any
hook's, and the hook's switch drops what is still pending. Only creation starts the check, so
the description written from the owner's answer asks nothing again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import LlmProvider
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import HookSpec, OnCommitted, RunAndAdvise, RunContext

from ...foundation.method import review_prompt
from .api import VALUE
from .model import Value
from .use_cases import VALUE_CREATED

VALUE_REVIEW_PROMPT = review_prompt(VALUE, "its name and description")

CLARIFY_REQUEST = (
    "New Values to clarify, each with advice:\n"
    "{values}\n"
    "Read the current Values first.\n"
    "If a Value is gone or its description already answers the advice, skip it.\n"
    "Say that you looked at the new Values again and they could be improved.\n"
    "For each Value, say briefly what could be clearer, then ask one short question, "
    "following its advice.\n"
    "Offer each suggestion as a possibility, not as the user's meaning.\n"
    "Offer to update the Value with their answer.\n"
    "When they answer, route to workspace_mutator to update the Value, "
    "or to propose a Goal or a repeating Action instead.\n"
    "Change nothing until they answer."
)


class ValueClearInput(ToolInput):
    """Nothing to add: the Value is clear."""


class ValueUnclearInput(ToolInput):
    advice: str = Field(description="What is unclear, and one advice for the question.")


VALUE_CLEAR_TERMINAL = TerminalTool("clear", "The Value is clear.", ValueClearInput)
VALUE_UNCLEAR_TERMINAL = TerminalTool(
    "unclear", "The Value needs clarification.", ValueUnclearInput
)


class ValueReviewer:
    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def advice(self, value: Mapping[str, Any]) -> str | None:
        """What to ask about the Value, or None when it is clear."""
        answer = await run_mini_session(
            self.provider,
            system_prompt=VALUE_REVIEW_PROMPT,
            context=json.dumps(value, ensure_ascii=False),
            terminals=(VALUE_CLEAR_TERMINAL, VALUE_UNCLEAR_TERMINAL),
            max_tool_calls=1,
            max_repairs=1,
            reasoning_effort="none",
        )
        if answer.name == VALUE_CLEAR_TERMINAL.name:
            return None
        return answer.payload.advice


class ValueReviewResources(Protocol):
    value_reviewer: ValueReviewer


async def created_value(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def _read_value(session: AsyncSession, value_id: int) -> dict[str, str] | None:
    value = await session.get(Value, value_id)
    if value is None:
        return None
    return {"name": value.name, "description": value.description}


async def review_value(
    value_id: int, context: RunContext[ValueReviewResources]
) -> tuple[dict[str, Any], ...]:
    """The new Value to ask about, with its advice, when it is unclear and still what the
    model read."""
    async with context.sessions() as session:
        asked = await _read_value(session, value_id)
    if asked is None:
        return ()
    advice = await context.resources.value_reviewer.advice(asked)
    if advice is None:
        return ()
    async with context.sessions() as session:
        if await _read_value(session, value_id) != asked:
            return ()
    return ({"id": value_id, "advice": advice},)


async def clarification_request(
    session: AsyncSession, items: Sequence[dict[str, Any]]
) -> str | None:
    """One request for every Value still there, or nothing when none is."""
    lines = []
    for item in items:
        value = await _read_value(session, item["id"])
        if value is not None:
            lines.append(f'- [{value["name"]}](value:{item["id"]}): {item["advice"]}')
    return CLARIFY_REQUEST.format(values="\n".join(lines)) if lines else None


VALUE_CLARIFICATION_HOOK = HookSpec(
    name="values.value_clarification",
    owner="values",
    on=(OnCommitted(kind=VALUE_CREATED),),
    evaluate=created_value,
    effect=RunAndAdvise(run=review_value, prepare=clarification_request),
    title="Value clarification",
    description=(
        "After a Value is created, checks whether it is a direction the user lives by and "
        "whether its description says what they do for it. Asks to clarify it, with advice, "
        "only when it does not."
    ),
)
