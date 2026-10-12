"""Clarify a new Value only when it is not a clear direction the owner acts on.

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

from .model import Value
from .use_cases import VALUE_CREATED

# Whether the Value is right for the owner is theirs to know: the name and description cannot show it.
VALUE_REVIEW_PROMPT = (
    "You review one new Value: its name and description.\n"
    "A Value is a direction the user acts on. It is never Done.\n\n"
    "A clear Value is:\n"
    "- a direction, not a result that can be Done;\n"
    "- how the user acts, not a feeling to have;\n"
    "- in the user's hands, not what others do or think;\n"
    "- toward something, not away from something;\n"
    "- an end, not a means such as money or a skill;\n"
    "- chosen, not a rule with must, always, never or perfect;\n"
    "- specific: the name or description says what exactly matters to the user.\n"
    "Do not judge whether the Value is right for the user.\n\n"
    "Examples:\n"
    '- "Health", "Sleep 7 hours, move every day": clear()\n'
    '- "Family", "Evenings with the kids, calls to my parents": clear()\n'
    '- "Family", no description: unclear("Does not say what exactly matters. '
    'Ask what in Family touches the user, and how it looks on an ordinary weekday.")\n'
    '- "Learn Spanish": unclear("A result that can be Done, so a Goal. '
    'Suggest a Goal and ask which Value it serves, such as Growth or Freedom.")\n'
    '- "Money": unclear("A means. Ask what it is for, and again, '
    'until a direction such as Security or Freedom.")\n'
    '- "Be happy": unclear("A feeling. Ask what the user does on a day they live it.")\n'
    '- "Always be productive": unclear("A rule with always. '
    'Ask whether it is want or must; suggest Craft.")\n'
    '- "Not be like my father": unclear("Away from something. '
    'Ask what the user wants instead.")\n'
    '- "Be respected": unclear("Others decide it. '
    "Ask what is in the user's hands, such as Dignity or Honesty.\")\n"
    '- "Running": unclear("A practice, a repeating Action. '
    'Ask what it serves, such as Health.")\n'
    "Call clear when every point holds.\n"
    "Otherwise call unclear with what is unclear and one advice."
)

CLARIFY_REQUEST = (
    "New Values to clarify, each with advice:\n"
    "{values}\n"
    "Read the current Values first.\n"
    "If a Value is gone or its description already answers the advice, skip it.\n"
    "Ask one short question per Value, following its advice.\n"
    "Offer each suggestion as a possibility, not as the user's meaning.\n"
    "When they answer, route to workspace_mutator to update the Value or propose a Goal.\n"
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
        "After a Value is created, checks whether it is a clear direction the user acts on. "
        "Asks to clarify it, with advice, only when it is not."
    ),
)
