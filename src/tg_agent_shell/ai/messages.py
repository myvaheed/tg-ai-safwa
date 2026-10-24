"""What a session reads before its own steps: the blocks, and what each one says.

The two rules about *where* a block may go — only ``messages[0]`` is a system message, and
the order is by how often each block changes so a remote provider can cache the head — are
`agent_runtime.context`'s, and this file uses them. What is in a block is the application's.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime import append_user_message, system_note, system_text

from .conversation import conversation_note
from .subagents import RoutedSubagent
from .tools import conversation_for

# What heads the receipts a routed subagent reads. A Discarded line is one of them, so they
# are not all saved.
PRIOR_RECEIPTS = "Already proposed in this request:"

# What heads the conversation a subsession's agent was opened from.
OPENED_FROM = (
    "The conversation you were opened from, newest last. None of it is yours: read it for "
    "what the user wants to talk about."
)


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


def ordered_owner_context(memory_text: str, workspace_state: str) -> str:
    """Put the explicit workspace state after the durable memory it can override."""
    return (
        f"Persistent memory:\n{memory_text}"
        f"\n\nCurrent workspace state:\n{workspace_state}"
    )


class ContextBuilder:
    """What each kind of session is given to read before its own steps."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        memory: Memory,
        workspace_state: Callable[[AsyncSession], Awaitable[StateBlocks]],
        *,
        system_prompt: str,
        subagents: dict[str, RoutedSubagent],
    ) -> None:
        self.sessions = sessions
        self.memory = memory
        # What the world looks like right now. The blocks are the application's, so the
        # builder is handed one rather than reaching into a feature for it.
        self.workspace_state = workspace_state
        self.system_prompt = system_prompt
        self.subagents = subagents

    async def messages_for(
        self,
        kind: str,
        dialogue: list[dict[str, Any]],
        prior_receipts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Rebuild the context prefix a session reads, from live state, by its kind."""
        routed = self.subagents.get(kind)
        if routed is not None and routed.title is not None:
            return await self.talk(routed, dialogue)
        if routed is not None:
            return await self.routed(routed, dialogue, prior_receipts)
        return await self.root(dialogue)

    async def root(self, dialogue: list[dict[str, Any]]) -> list[dict[str, Any]]:
        memory = await self.memory.sync()
        async with self.sessions() as session:
            context = await self.workspace_state(session)
        # Ordered by how often each block changes, so the stable prefix stays
        # byte-identical across turns and remote prompt caching can hit it.
        # Anything volatile goes after the dialogue, never into a system block.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            system_note(ordered_owner_context(memory.text, context.state)),
        ]
        _extend_with(messages, dialogue)
        append_user_message(messages, system_text(context.clock))
        return messages

    async def talk(
        self, talk: RoutedSubagent, dialogue: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """A subsession's agent reads the conversation it was opened from as data, and its
        own stretch of the chat in its own roles.

        The conversation it was opened from does not change while the subsession is open,
        so it stands before the state, which a Save changes, and the head stays cached for
        the whole subsession.
        """
        messages: list[dict[str, Any]] = [{"role": "system", "content": talk.prompt}]
        opened_from = (
            conversation_for(await talk.conversation(), talk.history_messages)
            if talk.conversation is not None
            else ""
        )
        if opened_from:
            append_user_message(messages, system_text(f"{OPENED_FROM}\n{opened_from}"))
        async with self.sessions() as session:
            context = await self.workspace_state(session)
        if talk.workspace_state:
            append_user_message(
                messages, system_text(f"Current workspace state:\n{context.state}")
            )
        _extend_with(messages, dialogue)
        if talk.current is not None and (now := await talk.current()):
            append_user_message(messages, system_text(now))
        append_user_message(messages, system_text(context.clock))
        return messages

    async def routed(
        self,
        routed: RoutedSubagent,
        dialogue: list[dict[str, Any]],
        prior_receipts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """A routed subagent reads the conversation as data, under its own prompt.

        Same order as the Advisor's: prompt, then state, then conversation, then what this
        turn has already proposed, then its own current values — so the stable part stays
        byte-identical and the volatile part stays last.  The receipts sit outside the
        conversation, so the window the subagent declared never trims them away.
        """
        messages: list[dict[str, Any]] = [{"role": "system", "content": routed.prompt}]
        if routed.workspace_state:
            async with self.sessions() as session:
                context = await self.workspace_state(session)
            append_user_message(
                messages, system_text(f"Current workspace state:\n{context.state}")
            )
        conversation = conversation_for(dialogue, routed.history_messages)
        if conversation:
            append_user_message(messages, conversation_note(conversation))
        lines = [line for line in prior_receipts or [] if line.strip()]
        if lines:
            append_user_message(
                messages, system_text(PRIOR_RECEIPTS + "\n" + "\n".join(lines))
            )
        if routed.current is not None and (now := await routed.current()):
            append_user_message(messages, system_text(now))
        return messages


def _extend_with(messages: list[dict[str, Any]], dialogue: list[dict[str, Any]]) -> None:
    """The dialogue in its own roles. The history source has already bounded the window by
    its token budget, and laid it out as the provider's own messages, earlier calls and
    results included."""
    for item in dialogue:
        if item["role"] == "user":
            append_user_message(messages, str(item["content"]))
        else:
            messages.append(dict(item))
