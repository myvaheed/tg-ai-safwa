"""Clarify a new Goal only when its result or its Done is unclear.

The hook's work checks each new Goal or Subgoal with one small model call and keeps the
unclear one as its pending request; every pending Goal is worded in one request, which waits
behind the Cue gate like any hook's, and the hook's switch drops what is still pending. Only
creation starts the check, so the Note written from the owner's answer asks nothing again.
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
    "You review one new Goal.\n"
    "Decide whether its result is specific and its Done is observable.\n\n"
    "Specific: the title names one concrete result, not a direction or an ongoing practice.\n"
    "Observable: the title or Note says what fact shows the Goal is Done.\n"
    "Use the title, Note, Deadline and parent Goal.\n"
    "Do not judge whether the Goal is attainable or worth doing.\n\n"
    "Examples:\n"
    '- "Run a half marathon": clear — finishing the race is observable.\n'
    '- "Improve my English", Note "Pass IELTS with 7.0": clear — the Note names the result.\n'
    '- "Learn Spanish": unclear — nothing says when it is Done.\n'
    '- "Get fit": unclear — a direction, not a result.\n'
    "Call goal_review with clear=false when the result is vague or Done is not observable.\n"
    "Otherwise call goal_review with clear=true."
)

CLARIFY_REQUEST = (
    "New Goals whose result or Done is unclear:\n"
    "{cards}\n"
    "Read the current Goals first.\n"
    "If a Goal is gone or its Note already says what fact shows it is Done, skip it.\n"
    "Briefly ask what concrete result each Goal is and how the user will see it is Done.\n"
    "If you have a useful possible criterion, suggest it briefly and ask "
    "whether it fits the user's meaning.\n"
    "Present it as a possibility, not as the user's criterion.\n"
    "When they answer, route to workspace_mutator to write the criterion in Note.\n"
    "Change nothing until they answer."
)


class GoalReviewInput(ToolInput):
    clear: bool = Field(description="The result is specific and its Done is observable.")


GOAL_REVIEW_TERMINAL = TerminalTool("goal_review", "Judge one new Goal.", GoalReviewInput)


class GoalReviewer:
    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def clear(self, goal: Mapping[str, Any]) -> bool:
        answer = await run_mini_session(
            self.provider,
            system_prompt=GOAL_REVIEW_PROMPT,
            context=json.dumps(goal, ensure_ascii=False),
            terminals=(GOAL_REVIEW_TERMINAL,),
            max_tool_calls=1,
            max_repairs=1,
            reasoning_effort="none",
        )
        return answer.payload.clear


class GoalReviewResources(Protocol):
    goal_reviewer: GoalReviewer


async def created_card(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def _read_goal(session: AsyncSession, card_id: int) -> dict[str, Any] | None:
    """What the review reads of an open Goal or Subgoal, or None for anything else.

    A Goal carrying the Inbox Tag is a capture, kept without detail on purpose.
    """
    card = await session.get(Card, card_id)
    if (
        card is None
        or card.kind != CardKind.GOAL.value
        or card.archived_at is not None
        or card.effective_stage in {stage.value for stage in TERMINAL_STAGES}
    ):
        return None
    inbox = await session.scalar(
        select(CardTag.card_id)
        .join(Tag, Tag.id == CardTag.tag_id)
        .where(CardTag.card_id == card_id, Tag.name == INBOX_TAG_NAME)
    )
    if inbox is not None:
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
) -> tuple[int, ...]:
    """The new Goal to ask about when it is unclear and still what the model read."""
    async with context.sessions() as session:
        asked = await _read_goal(session, card_id)
    if asked is None or await context.resources.goal_reviewer.clear(asked):
        return ()
    async with context.sessions() as session:
        if await _read_goal(session, card_id) != asked:
            return ()
    return (card_id,)


async def clarification_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """One request for every Goal still open, or nothing when none is."""
    lines = []
    for item in items:
        goal = await _read_goal(session, int(item))
        if goal is not None:
            lines.append(f"- #{item} «{goal['title']}» ({goal['kind']})")
    return CLARIFY_REQUEST.format(cards="\n".join(lines)) if lines else None


GOAL_CLARIFICATION_HOOK = HookSpec(
    name="cards.goal_clarification",
    owner="cards",
    on=(OnCommitted(kind=CARD_CREATED),),
    evaluate=created_card,
    effect=RunAndAdvise(run=review_goal, prepare=clarification_request),
    title="Goal clarification",
    description=(
        "After a Goal or Subgoal is created, checks whether its result is specific and its Done "
        "observable. Asks to clarify Note only when they are not."
    ),
)
