"""The message prefix a session reads, rebuilt from live state on every turn.

Two rules govern this file and nothing else does. Only ``messages[0]`` is a system message,
because the Qwen3.5 chat template raises on a second one. And the blocks are ordered by how
often each changes, so the stable head stays byte-identical between turns and a remote
provider can cache it — which is why anything volatile goes after the dialogue.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..features.continuity.memory import MemoryFileStore
from .context import DialogueMessage, board_context, ordered_owner_context
from .subagents import RoutedSubagent
from .tools import conversation_for


def system_note(content: str) -> dict[str, Any]:
    """Carry a system block as owner text.

    Only ``messages[0]`` may be a system message: the Qwen3.5 chat template raises
    ``System message must be at the beginning`` on any later one.
    """

    return {"role": "user", "content": f"[System]: {content}"}


def _append_user_message(messages: list[dict[str, Any]], content: str) -> None:
    """Append user-side context without creating adjacent user turns."""
    if messages and messages[-1].get("role") == "user":
        messages[-1]["content"] += "\n" + content
        return
    messages.append({"role": "user", "content": content})


def _cache_breakpoint(message: dict[str, Any]) -> dict[str, Any]:
    """Mark the end of a reusable prefix.

    OpenRouter accepts the Anthropic form and converts it to OpenAI's
    ``prompt_cache_breakpoint`` for GPT-5.6 and newer, so one marker is portable.
    """

    content = message.get("content")
    if not isinstance(content, str) or not content:
        return message
    return {
        **message,
        "content": [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ],
    }


class ContextBuilder:
    """What each kind of session is given to read before its own steps."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        memory: MemoryFileStore,
        *,
        system_prompt: str,
        subagents: dict[str, RoutedSubagent],
        cache_breakpoints: bool = False,
    ) -> None:
        self.sessions = sessions
        self.memory = memory
        self.system_prompt = system_prompt
        self.subagents = subagents
        self.cache_breakpoints = cache_breakpoints

    async def for_session(
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
            context = await board_context(session)
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
                _append_user_message(messages, item.content)
            else:
                messages.append({"role": item.role, "content": item.content})
        _append_user_message(messages, f"[System]: {context.clock}")
        if self.cache_breakpoints:
            messages[0] = _cache_breakpoint(messages[0])
            if len(messages) > 2:
                messages[-2] = _cache_breakpoint(messages[-2])
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
                context = await board_context(session)
            _append_user_message(messages, f"[System]: Current board state:\n{context.state}")
        conversation = conversation_for(dialogue)
        if conversation:
            _append_user_message(
                messages,
                "[System]: The conversation so far, newest last. None of it is yours: read it "
                f"for what the owner wants changed.\n{conversation}",
            )
        if self.cache_breakpoints:
            messages[0] = _cache_breakpoint(messages[0])
        lines = [line for line in prior_receipts or [] if line.strip()]
        if lines:
            _append_user_message(
                messages, "[System]: Already saved in this request:\n" + "\n".join(lines)
            )
        if routed.clock is not None:
            _append_user_message(messages, f"[System]: {routed.clock()}")
        return messages
