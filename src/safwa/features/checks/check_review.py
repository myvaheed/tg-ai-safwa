"""Advise on a new Check only when it does not meet the Check criteria.

The hook's work reads each new Check with the Card it is on, and one small model call answers
clear, or unclear with its advice. The unclear one is kept with that advice as its pending
request; every pending Check is worded in one request, which waits behind the Cue gate like
any hook's, and the hook's switch drops what is still pending. Only creation starts the
check, and the next instance of a repeating Check is not a creation, so the change made from
the owner's answer asks nothing again.
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

from ...foundation.method import review_prompt
from ..cards.model import Card, CardCheck, card_kind_name
from .api import CHECK
from .model import Check
from .use_cases import CHECK_CREATED

# Whether the Check is worth keeping is the owner's to know: its title cannot show it.
CHECK_REVIEW_PROMPT = review_prompt(CHECK, "its title, Note, Schedule and the Card it is on")

CHECK_REVIEW_REQUEST = (
    "New Checks to improve, each with advice:\n"
    "{checks}\n"
    "Read the current Checks first.\n"
    "If a Check is gone, answered or already follows the advice, skip it.\n"
    "Say that you looked at the new Checks again and they could be improved.\n"
    "For each Check, say briefly what to change and why, following its advice.\n"
    "Offer each change as a possibility, not as the user's choice.\n"
    "Ask whether to make the changes.\n"
    "When they answer, route to workspace_mutator to update the Check, "
    "or to propose an Action instead.\n"
    "Change nothing until they answer."
)


class CheckClearInput(ToolInput):
    """Nothing to change: the Check fits."""


class CheckUnclearInput(ToolInput):
    advice: str = Field(description="What to change, why, and one advice.")


CHECK_CLEAR_TERMINAL = TerminalTool("clear", "The Check fits.", CheckClearInput)
CHECK_UNCLEAR_TERMINAL = TerminalTool("unclear", "The Check needs a change.", CheckUnclearInput)


class CheckReviewer:
    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def advice(self, check: Mapping[str, Any]) -> str | None:
        """What to change in the Check, or None when it fits."""
        answer = await run_mini_session(
            self.provider,
            system_prompt=CHECK_REVIEW_PROMPT,
            context=json.dumps(check, ensure_ascii=False),
            terminals=(CHECK_CLEAR_TERMINAL, CHECK_UNCLEAR_TERMINAL),
            max_tool_calls=1,
            max_repairs=1,
            reasoning_effort="none",
        )
        if answer.name == CHECK_CLEAR_TERMINAL.name:
            return None
        return answer.payload.advice


class CheckReviewResources(Protocol):
    check_reviewer: CheckReviewer


async def created_check(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def _read_check(session: AsyncSession, check_id: int) -> dict[str, Any] | None:
    """What the review reads of an open Check, or None for anything else."""
    check = await session.get(Check, check_id)
    if check is None or check.archived_at is not None or check.outcome is not None:
        return None
    card = await session.scalar(
        select(Card).join(CardCheck, CardCheck.card_id == Card.id).where(
            CardCheck.check_id == check_id
        )
    )
    return {
        "title": check.title,
        "note": check.note,
        "schedule": check.schedule or "",
        "card": f"{card_kind_name(card.kind, card.parent_id)} «{card.title}»" if card else "",
    }


async def review_check(
    check_id: int, context: RunContext[CheckReviewResources]
) -> tuple[dict[str, Any], ...]:
    """The new Check to advise on, with its advice, when something clearly needs a change and
    it is still what the model read."""
    async with context.sessions() as session:
        asked = await _read_check(session, check_id)
    if asked is None:
        return ()
    advice = await context.resources.check_reviewer.advice(asked)
    if advice is None:
        return ()
    async with context.sessions() as session:
        if await _read_check(session, check_id) != asked:
            return ()
    return ({"id": check_id, "advice": advice},)


async def check_review_request(
    session: AsyncSession, items: Sequence[dict[str, Any]]
) -> str | None:
    """One request for every Check still open, or nothing when none is."""
    lines = []
    for item in items:
        check = await _read_check(session, item["id"])
        if check is not None:
            lines.append(f"- [{check['title']}](check:{item['id']}): {item['advice']}")
    return CHECK_REVIEW_REQUEST.format(checks="\n".join(lines)) if lines else None


CHECK_REVIEW_HOOK = HookSpec(
    name="checks.check_review",
    owner="checks",
    on=(OnCommitted(kind=CHECK_CREATED),),
    evaluate=created_check,
    effect=RunAndAdvise(run=review_check, prepare=check_review_request),
    title="Check review",
    description=(
        "After a Check is created, checks that it observes a state or a result rather than "
        "work done, that it is answered yes or no, and that it is about its Card's result. "
        "Suggests changes, with reasons, only when something clearly does not fit."
    ),
)
