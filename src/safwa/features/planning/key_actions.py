"""Which of a Sprint's Actions its Success criterion rests on: asked of the model in
batches, read back as one yes or no per number, and kept as a mark on the commitment."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import CompletionRequest, LlmProvider
from tg_agent_shell.foundation.changes import record_change

from ..cards.model import Card
from .api import SPRINT_KEY_ACTIONS
from .model import Sprint, SprintCommitment, SprintStatus

logger = logging.getLogger(__name__)

# How many Actions one question to the model holds; the batches are asked at once.
KEY_BATCH = 10

KEY_ACTIONS_PROMPT = (
    "You are given a Sprint's Success criterion and a numbered list of its Actions.\n"
    "For each Action, answer whether the criterion cannot be reached without it.\n"
    "Answer one line per Action: the number, a colon, then yes or no. Nothing else."
)

_ANSWER = re.compile(r"^\s*(\d+)\s*[:.)\-]\s*(yes|no)\b", re.IGNORECASE | re.MULTILINE)


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
        """The indices into `titles` the model said yes to, or None when no answer could
        be read; a batch that could not be read marks nothing and is logged."""
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
                temperature=0.1,
            )
        )
        read = {
            int(number): answer.lower() == "yes"
            for number, answer in _ANSWER.findall(turn.content)
            if 1 <= int(number) <= len(batch)
        }
        if not read:
            logger.warning("The key Actions answer could not be read: %r", turn.content)
            return None
        return {number - 1 for number, yes in read.items() if yes}
