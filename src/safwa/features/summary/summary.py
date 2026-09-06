"""The object that turns the dialogue into a Summary.

One long-lived collaborator, built once by the composition root and reached from
`api.py`. It reads the canonical conversation, calls the provider, and hands the words to
whoever asked for them; it owns no schedule and no state of its own.

It also owns no lock. `TurnManager.run_background` is the single lease every caller takes,
and it already refuses a second background run — a lock here would be a second mechanism
for the property the turn exists to hold.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from llm_gateway import CompletionRequest, LlmProvider
from tg_agent_shell.foundation.kinds import MessageKind

from ...constants import SUMMARY_TRIGGER_TOKENS
from ...foundation.tokens import TOKEN_CHARS_ESTIMATE, estimate_tokens
from .agent import SUMMARY_PROMPT
from .window import SUMMARY_HEADER

logger = logging.getLogger(__name__)


class DialogueHistory(Protocol):
    async def recent(self, chat_id: int, **kwargs: Any) -> list[Any]: ...


class DialogueSummary:
    def __init__(
        self,
        history: DialogueHistory,
        provider: LlmProvider,
        *,
        summary_trigger_tokens: int = SUMMARY_TRIGGER_TOKENS,
        chars_per_token: float = TOKEN_CHARS_ESTIMATE,
    ) -> None:
        self.history = history
        self.provider = provider
        self.summary_trigger_tokens = summary_trigger_tokens
        self.chars_per_token = chars_per_token

    async def close_window(
        self,
        chat_id: int,
        write: Callable[[str], Awaitable[None]],
        *,
        force: bool = False,
        still_current: Callable[[], bool] | None = None,
    ) -> bool:
        """Write a Summary if the dialogue has outgrown the budget, and say whether it did."""
        entries = await self.history.recent(chat_id)
        # The previous Summary is rewritten rather than dropped: the window keeps only
        # the newest one, so anything it alone remembers would be lost with it.
        previous = next(
            (entry for entry in entries if entry.kind == MessageKind.SUMMARY.value), None
        )
        dialogue = "\n".join(
            f"[{entry.role}]: {entry.text}"
            for entry in entries
            if entry.kind != MessageKind.SUMMARY.value and not entry.before_edge
        )
        tokens = estimate_tokens(dialogue, self.chars_per_token)
        if not dialogue or (not force and tokens < self.summary_trigger_tokens):
            return False
        # The window already labelled the previous Summary as what it is; saying so a
        # second time is one more line for a small model to reconcile.
        request = (
            f"{previous.text}\n\nDialogue since it:\n{dialogue}" if previous else dialogue
        )
        summary = (
            await self.provider.complete(
                CompletionRequest(
                    messages=(
                        {"role": "system", "content": SUMMARY_PROMPT},
                        {"role": "user", "content": request},
                    ),
                    temperature=0.1,
                )
            )
        ).content
        if still_current is not None and not still_current():
            return False
        current_entries = await self.history.recent(chat_id)
        snapshot = [(entry.message_id, entry.kind, entry.text) for entry in entries]
        current = [(entry.message_id, entry.kind, entry.text) for entry in current_entries]
        if current != snapshot:
            logger.info("Discarding a stale automatic summary")
            return False
        await write(f"{SUMMARY_HEADER}\n{summary}")
        return True
