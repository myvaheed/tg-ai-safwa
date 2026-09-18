"""Which of a Sprint's Actions its Success criterion rests on: asked of the model in
batches, answered as one tool call naming their numbers, and kept as a mark on the
commitment."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import CompletionRequest, LlmProvider
from tg_agent_shell.ai.contracts import ToolInput, tool_json_schema
from tg_agent_shell.foundation.changes import record_change

from ..cards.model import Card
from .api import SPRINT_KEY_ACTIONS
from .model import Sprint, SprintCommitment, SprintStatus

logger = logging.getLogger(__name__)

# How many Actions one question to the model holds; the batches are asked at once.
KEY_BATCH = 10

KEY_ACTIONS_PROMPT = (
    "You are given a Sprint's Success criterion and a numbered list of its Actions.\n"
    "Call mark_key_actions with the numbers of the Actions the criterion cannot be reached "
    "without. Pass an empty list when there is none."
)


class KeyActionsInput(ToolInput):
    key: list[int] = Field(
        description="The numbers of the Actions the criterion cannot be reached without."
    )


# The one call the model answers the question with: the numbers, and nothing to parse.
KEY_ACTIONS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "mark_key_actions",
        "description": "Name the Actions the Success criterion cannot be reached without.",
        "parameters": tool_json_schema(KeyActionsInput),
    },
}


class KeyActions:
    """The classifier, on the one provider the application has."""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def mark(
        self, session: AsyncSession, sprint_id: int, *, card_ids: Sequence[int] | None = None
    ) -> None:
        """Mark which of the Sprint's open Actions — or only these — its criterion rests on.

        The marks are handed on as one fact once they are written; an answer that could not
        be read at all writes nothing and hands nothing on.
        """
        sprint = await session.get(Sprint, sprint_id)
        if sprint is None or sprint.status != SprintStatus.ACTIVE.value:
            return
        only = [SprintCommitment.card_id.in_(card_ids)] if card_ids is not None else []
        rows = list(
            await session.execute(
                select(SprintCommitment, Card.title)
                .join(Card, Card.id == SprintCommitment.card_id)
                .where(
                    SprintCommitment.sprint_id == sprint_id,
                    SprintCommitment.removed_at.is_(None),
                    SprintCommitment.result.is_(None),
                    Card.archived_at.is_(None),
                    *only,
                )
                .order_by(SprintCommitment.card_id)
            )
        )
        if not rows:
            return
        key = await self.classify(sprint.success_criteria, [title for _, title in rows])
        if key is None:
            return
        for index, (commitment, _) in enumerate(rows):
            commitment.key_action = index in key
        record_change(session, SPRINT_KEY_ACTIONS, sprint_id)

    async def classify(self, criterion: str, titles: Sequence[str]) -> set[int] | None:
        """The indices into `titles` the model named, or None when no answer could be
        read; a batch that could not be read marks nothing and is logged."""
        batches = [
            (start, titles[start : start + KEY_BATCH]) for start in range(0, len(titles), KEY_BATCH)
        ]
        answers = await asyncio.gather(*(self._ask(criterion, batch) for _, batch in batches))
        if all(answer is None for answer in answers):
            return None
        return {
            start + index
            for (start, _), answer in zip(batches, answers, strict=True)
            if answer is not None
            for index in answer
        }

    async def _ask(self, criterion: str, batch: Sequence[str]) -> set[int] | None:
        listed = "\n".join(f"{index + 1}. {title}" for index, title in enumerate(batch))
        turn = await self.provider.complete(
            CompletionRequest(
                messages=(
                    {"role": "system", "content": KEY_ACTIONS_PROMPT},
                    {
                        "role": "user",
                        "content": f"Success criterion: {criterion}\nActions:\n{listed}",
                    },
                ),
                tools=(KEY_ACTIONS_TOOL,),
                tool_choice="required",
                temperature=0.1,
            )
        )
        call = next((call for call in turn.tool_calls if call.name == "mark_key_actions"), None)
        if call is None:
            logger.warning("The key Actions answer was not the call: %r", turn.content)
            return None
        try:
            numbers = KeyActionsInput.model_validate_json(call.arguments_json).key
        except ValidationError as error:
            logger.warning("The key Actions call could not be read: %s", error)
            return None
        return {number - 1 for number in numbers if 1 <= number <= len(batch)}
