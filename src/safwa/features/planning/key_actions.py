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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

# One row as the model was asked about it: the commitment, and the title it read.
Asked = tuple[int, str]


class KeyActions:
    """The classifier, on the one provider the application has."""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def mark(
        self,
        sessions: async_sessionmaker[AsyncSession],
        sprint_id: int,
        *,
        card_ids: Sequence[int] | None = None,
    ) -> None:
        """Mark which of the Sprint's open Actions — or only these — its criterion rests on.

        The rows are read and the read closed before the model is asked, and each answer is
        written only to a row that is still what was asked about: open in the running Sprint,
        under the title the model read. Two answers may arrive in either order, and one about
        an Action renamed meanwhile is about something else. The marks are handed on as one
        fact once any is written; a batch whose answer could not be read writes nothing.
        """
        async with sessions() as session:
            sprint = await session.get(Sprint, sprint_id)
            if sprint is None or sprint.status != SprintStatus.ACTIVE.value:
                return
            criterion = sprint.success_criteria
            asked = await _open_rows(session, sprint_id, card_ids)
        if not asked:
            return
        marks = await self.classify(criterion, [title for _, title in asked])
        if not marks:
            return
        async with sessions() as session:
            sprint = await session.get(Sprint, sprint_id)
            if sprint is None or sprint.status != SprintStatus.ACTIVE.value:
                return
            written = False
            for index, key in marks.items():
                commitment_id, title = asked[index]
                commitment = await session.get(SprintCommitment, commitment_id)
                if commitment is None or commitment.removed_at is not None or commitment.result is not None:
                    continue
                card = await session.get(Card, commitment.card_id)
                if card is None or card.title != title:
                    continue
                commitment.key_action = key
                written = True
            if written:
                record_change(session, SPRINT_KEY_ACTIONS, sprint_id)
            await session.commit()

    async def classify(self, criterion: str, titles: Sequence[str]) -> dict[int, bool]:
        """Whether each of `titles` was named, by index, for every batch whose answer could
        be read; a batch that could not be read is absent, and logged."""
        batches = [
            (start, titles[start : start + KEY_BATCH]) for start in range(0, len(titles), KEY_BATCH)
        ]
        answers = await asyncio.gather(*(self._ask(criterion, batch) for _, batch in batches))
        return {
            start + index: index in answer
            for (start, batch), answer in zip(batches, answers, strict=True)
            if answer is not None
            for index in range(len(batch))
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
        if any(number < 1 or number > len(batch) for number in numbers):
            logger.warning("The key Actions call named a number not in the list: %r", numbers)
            return None
        return {number - 1 for number in numbers}


async def _open_rows(
    session: AsyncSession, sprint_id: int, card_ids: Sequence[int] | None
) -> list[Asked]:
    """The Sprint's open commitments — or only these Cards' — each with its Action's title."""
    only = [SprintCommitment.card_id.in_(card_ids)] if card_ids is not None else []
    rows = await session.execute(
        select(SprintCommitment.id, Card.title)
        .join(Card, Card.id == SprintCommitment.card_id)
        .where(
            SprintCommitment.sprint_id == sprint_id,
            SprintCommitment.removed_at.is_(None),
            SprintCommitment.result.is_(None),
            *only,
        )
        .order_by(SprintCommitment.card_id)
    )
    return [(commitment_id, title) for commitment_id, title in rows]
