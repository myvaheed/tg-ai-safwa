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

# What the interface says on the user's side is closed by a tag, so a model reads where it
# ends and the person's own words begin again.
SYSTEM_OPEN = "<system>"
SYSTEM_CLOSE = "</system>"


def system_text(content: str) -> str:
    """Words the interface says, as user text that says where it ends."""
    return f"{SYSTEM_OPEN}{content}{SYSTEM_CLOSE}"


def system_note(content: str) -> dict[str, Any]:
    """Carry a system block as user text.

    Only ``messages[0]`` may be a system message: a chat template that enforces this
    raises ``System message must be at the beginning`` on any later one.
    """

    return {"role": "user", "content": system_text(content)}


def append_user_message(messages: list[dict[str, Any]], content: str) -> None:
    """Append user-side context without creating adjacent user turns."""
    if messages and messages[-1].get("role") == "user":
        messages[-1]["content"] += "\n" + content
        return
    messages.append({"role": "user", "content": content})
