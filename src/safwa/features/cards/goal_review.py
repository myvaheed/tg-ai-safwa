"""Clarify a new Goal only when it is not one concrete result with an observable Done.

The hook's work checks each new Goal or Subgoal with one small model call, which answers
clear or unclear with its advice, and keeps the unclear one with that advice as its pending
request; every pending Goal is worded in one request, which waits behind the Cue gate like
any hook's, and the hook's switch drops what is still pending. Only creation starts the
check, so the Note written from the owner's answer asks nothing again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import LlmProvider
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import HookSpec, OnCommitted, RunAndAdvise, RunContext

from ...constants import INBOX_TAG_NAME
from ..tags.model import CardTag, Tag
from .model import TERMINAL_STAGES, Card, CardKind, card_kind_name
from .use_cases import CARD_CREATED

# Attainable and meaningful are the owner's to know: the title and Note cannot show them.
GOAL_REVIEW_PROMPT = (
    "You review one new Goal: its title, Note, Deadline and parent Goal.\n"
    "A Goal is one result that takes more than one day and can be Done.\n\n"
    "A clear Goal:\n"
    "- names one concrete result, not a direction;\n"
    "- is a result, not an ongoing practice;\n"
    "- takes more than one day; work for one day is an Action;\n"
    "- has an observable Done: the title or Note says what fact shows it.\n"
    "Do not judge whether the Goal is attainable or worth doing.\n\n"
    "Examples:\n"
    '- "Run a half marathon": clear()\n'
    '- "Improve my English", Note "Pass IELTS with 7.0": clear()\n'
    '- "Learn Spanish": unclear("Done is not observable. '
    'Ask what result shows it, such as passing DELE B2.")\n'
    '- "Get fit": unclear("A direction, not a result. Ask what result would show it, '
    'such as a 5 km run under 30 minutes; else suggest a Value such as Health.")\n'
    '- "Meditate daily": unclear("An ongoing practice. Suggest a repeating Action; '
    'ask whether a result is behind it, such as 30 days in a row.")\n'
    '- "Call the bank": unclear("Fits in one day. Suggest an Action instead.")\n'
    '- "Launch the shop and find a job": unclear("Two results. Suggest two Goals.")\n'
    "Call clear when every point holds.\n"
    "Otherwise call unclear with what is unclear and one advice."
)

CLARIFY_REQUEST = (
    "New Goals to clarify, each with advice:\n"
    "{cards}\n"
    "Read the current Goals first.\n"
    "If a Goal is gone or its Note already answers the advice, skip it.\n"
    "Say that you looked at the new Goals again and they could be improved.\n"
    "For each Goal, say briefly what could be clearer, then ask one short question, "
    "following its advice.\n"
    "Offer each suggestion as a possibility, not as the user's meaning.\n"
    "Offer to update the Goal with their answer.\n"
    "When they answer, route to workspace_mutator to write the criterion in Note, "
    "or to propose what it should be instead.\n"
    "Change nothing until they answer."
)


class GoalClearInput(ToolInput):
    """Nothing to add: the Goal is clear."""


class GoalUnclearInput(ToolInput):
    advice: str = Field(description="What is unclear, and one advice for the question.")


GOAL_CLEAR_TERMINAL = TerminalTool("clear", "The Goal is clear.", GoalClearInput)
GOAL_UNCLEAR_TERMINAL = TerminalTool("unclear", "The Goal needs clarification.", GoalUnclearInput)


class GoalReviewer:
    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def advice(self, goal: Mapping[str, Any]) -> str | None:
        """What to ask about the Goal, or None when it is clear."""
        answer = await run_mini_session(
            self.provider,
            system_prompt=GOAL_REVIEW_PROMPT,
            context=json.dumps(goal, ensure_ascii=False),
            terminals=(GOAL_CLEAR_TERMINAL, GOAL_UNCLEAR_TERMINAL),
            max_tool_calls=1,
            max_repairs=1,
            reasoning_effort="none",
        )
        if answer.name == GOAL_CLEAR_TERMINAL.name:
            return None
        return answer.payload.advice


class GoalReviewResources(Protocol):
    goal_reviewer: GoalReviewer


async def created_card(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def open_card(session: AsyncSession, card_id: int, kind: CardKind) -> Card | None:
    """The Card a new-item review reads: open, not archived, of this kind and not a capture.

    A Card carrying the Inbox Tag is a capture, kept without detail on purpose.
    """
    card = await session.get(Card, card_id)
    if (
        card is None
        or card.kind != kind.value
        or card.archived_at is not None
        or card.effective_stage in {stage.value for stage in TERMINAL_STAGES}
    ):
        return None
    inbox = await session.scalar(
        select(CardTag.card_id)
        .join(Tag, Tag.id == CardTag.tag_id)
        .where(CardTag.card_id == card_id, Tag.name == INBOX_TAG_NAME)
    )
    return card if inbox is None else None


async def _read_goal(session: AsyncSession, card_id: int) -> dict[str, Any] | None:
    """What the review reads of an open Goal or Subgoal, or None for anything else."""
    card = await open_card(session, card_id, CardKind.GOAL)
    if card is None:
        return None
    parent = await session.get(Card, card.parent_id) if card.parent_id else None
    return {
        "kind": card_kind_name(card.kind, card.parent_id),
        "title": card.title,
        "note": card.note,
        "deadline": card.schedule or "",
        "parent": parent.title if parent else "",
    }


async def review_goal(
    card_id: int, context: RunContext[GoalReviewResources]
) -> tuple[dict[str, Any], ...]:
    """The new Goal to ask about, with its advice, when it is unclear and still what the
    model read."""
    async with context.sessions() as session:
        asked = await _read_goal(session, card_id)
    if asked is None:
        return ()
    advice = await context.resources.goal_reviewer.advice(asked)
    if advice is None:
        return ()
    async with context.sessions() as session:
        if await _read_goal(session, card_id) != asked:
            return ()
    return ({"id": card_id, "advice": advice},)


async def clarification_request(
    session: AsyncSession, items: Sequence[dict[str, Any]]
) -> str | None:
    """One request for every Goal still open, or nothing when none is."""
    lines = []
    for item in items:
        goal = await _read_goal(session, item["id"])
        if goal is not None:
            lines.append(f"- #{item['id']} «{goal['title']}» ({goal['kind']}): {item['advice']}")
    return CLARIFY_REQUEST.format(cards="\n".join(lines)) if lines else None


GOAL_CLARIFICATION_HOOK = HookSpec(
    name="cards.goal_clarification",
    owner="cards",
    on=(OnCommitted(kind=CARD_CREATED),),
    evaluate=created_card,
    effect=RunAndAdvise(run=review_goal, prepare=clarification_request),
    title="Goal clarification",
    description=(
        "After a Goal or Subgoal is created, checks whether it is one concrete result that "
        "takes more than one day, with an observable Done. Asks to clarify it, with advice, "
        "only when it is not."
    ),
)
