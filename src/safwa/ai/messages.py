"""What a session reads before its own steps: the blocks, and what each one says.

The two rules about *where* a block may go — only ``messages[0]`` is a system message, and
the order is by how often each block changes so a remote provider can cache the head — are
`agent_runtime.context`'s, and this file uses them. What the blocks contain is Safwa's.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime import append_user_message, cache_breakpoint, system_note
from telegram_llm import DialogueMessage

from .subagents import RoutedSubagent
from .tools import conversation_for


class Memory(Protocol):
    """What the durable block is read from. Where the facts are kept is not the engine's."""

    async def sync(self) -> Facts: ...


class Facts(Protocol):
    text: str


@dataclass(frozen=True)
class StateBlocks:
    """Split so the volatile clock can be sent after the cacheable prefix."""

    state: str
    clock: str


def ordered_owner_context(memory_text: str, board_state: str) -> str:
    """Put the explicit board state after the durable memory it can override."""
    return (
        f"Persistent memory:\n{memory_text}"
        f"\n\nCurrent board state:\n{board_state}"
    )


class ContextBuilder:
    """What each kind of session is given to read before its own steps."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        memory: Memory,
        board_state: Callable[[AsyncSession], Awaitable[StateBlocks]],
        *,
        system_prompt: str,
        subagents: dict[str, RoutedSubagent],
        cache_breakpoints: bool = False,
    ) -> None:
        self.sessions = sessions
        self.memory = memory
        # What the world looks like right now. The blocks are the application's, so the
        # builder is handed one rather than reaching into a feature for it.
        self.board_state = board_state
        self.system_prompt = system_prompt
        self.subagents = subagents
        self.cache_breakpoints = cache_breakpoints

    async def messages_for(
        self,
        kind: str,
        dialogue: list[dict[str, Any]],
        prior_receipts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Rebuild the context prefix a session reads, from live state, by its kind."""
        routed = self.subagents.get(kind)
        if routed is not None:
            return await self.routed(routed, dialogue, prior_receipts)
        return await self.advisor(
            [
                DialogueMessage(role=str(item["role"]), content=str(item["content"]))
                for item in dialogue
            ]
        )

    async def advisor(self, dialogue: list[DialogueMessage]) -> list[dict[str, Any]]:
        memory = await self.memory.sync()
        async with self.sessions() as session:
            context = await self.board_state(session)
        # Ordered by how often each block changes, so the stable prefix stays
        # byte-identical across turns and remote prompt caching can hit it.
        # Anything volatile goes after the dialogue, never into a system block.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            system_note(ordered_owner_context(memory.text, context.state)),
        ]
        # The history source has already bounded the window by its token budget.
        for item in dialogue:
            if item.role == "user":
                append_user_message(messages, item.content)
            else:
                messages.append({"role": item.role, "content": item.content})
        append_user_message(messages, f"[System]: {context.clock}")
        if self.cache_breakpoints:
            messages[0] = cache_breakpoint(messages[0])
            if len(messages) > 2:
                messages[-2] = cache_breakpoint(messages[-2])
        return messages

    async def routed(
        self,
        routed: RoutedSubagent,
        dialogue: list[dict[str, Any]],
        prior_receipts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """A routed subagent reads the conversation as data, under its own prompt.

        Same order as the Advisor's: prompt, then state, then conversation, then what this
        turn has already saved, then the clock — so the stable part stays byte-identical
        and the volatile part stays last.  The receipts sit outside the conversation, so
        the ``SUBAGENT_HISTORY_LAST_MESSAGES`` window never trims them away.
        """
        messages: list[dict[str, Any]] = [{"role": "system", "content": routed.prompt}]
        if routed.board_state:
            async with self.sessions() as session:
                context = await self.board_state(session)
            append_user_message(messages, f"[System]: Current board state:\n{context.state}")
        conversation = conversation_for(dialogue)
        if conversation:
            append_user_message(
                messages,
                "[System]: The conversation so far, newest last. None of it is yours: read it "
                f"for what the owner wants changed.\n{conversation}",
            )
        if self.cache_breakpoints:
            messages[0] = cache_breakpoint(messages[0])
        lines = [line for line in prior_receipts or [] if line.strip()]
        if lines:
            append_user_message(
                messages, "[System]: Already saved in this request:\n" + "\n".join(lines)
            )
        if routed.clock is not None:
            append_user_message(messages, f"[System]: {routed.clock()}")
        return messages
