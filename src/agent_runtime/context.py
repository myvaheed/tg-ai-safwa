"""How a message list is ordered, and where its reusable part ends.

Two rules, and they are the reason this is a file rather than three loose helpers. Only
``messages[0]`` is a system message, because chat templates in the wild raise on a second
one. And the blocks are ordered by how often each changes, so the stable head stays
byte-identical between turns and a provider can cache it — which is why anything volatile
is appended after the dialogue, never folded into a system block.

What the blocks *say* is the host's, through `ContextSource`.
"""

from __future__ import annotations

from typing import Any


def system_note(content: str) -> dict[str, Any]:
    """Carry a system block as user text.

    Only ``messages[0]`` may be a system message: a chat template that enforces this
    raises ``System message must be at the beginning`` on any later one.
    """

    return {"role": "user", "content": f"[System]: {content}"}


def append_user_message(messages: list[dict[str, Any]], content: str) -> None:
    """Append user-side context without creating adjacent user turns."""
    if messages and messages[-1].get("role") == "user":
        messages[-1]["content"] += "\n" + content
        return
    messages.append({"role": "user", "content": content})


def cache_breakpoint(message: dict[str, Any]) -> dict[str, Any]:
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
