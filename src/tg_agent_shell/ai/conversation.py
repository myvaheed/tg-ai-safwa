"""The dialogue as data, for a reader that did not take part in it."""

from __future__ import annotations

import re
from collections.abc import Sequence

from telegram_llm import DialogueMessage

# Who each line of the dialogue belongs to.  The keys are the labels the window writes;
# the values are what a reader that did not take part sees.
CONVERSATION_TAGS = {
    "User": "User",
    "Assistant": "Advisor",
    "Summary": "Summary",
    "Tool result": "ToolResult",
}
_CONVERSATION_LINE = re.compile(
    r"^(?:\[(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] )?"
    r"(?:\[(?P<label>User|Assistant|Summary|Tool result)\]: )?"
)


def conversation_block(dialogue: Sequence[DialogueMessage]) -> str:
    """The dialogue as tagged data rather than as the reader's own turns.

    The Advisor is the assistant of this conversation and reads the roles as they are.
    Anyone routed into it is not: prose in the `assistant` slot would be a standing
    demonstration of answering in prose, which is the one thing a subagent must not do.
    Every line says whose it is instead, and none of them is the reader's own.
    """
    elements: list[tuple[str, str | None, list[str]]] = []
    for item in dialogue:
        spoken_by = "Advisor" if item.role == "assistant" else "User"
        for line in item.content.splitlines():
            match = _CONVERSATION_LINE.match(line)
            stamp, label = match.group("stamp"), match.group("label")
            tag = CONVERSATION_TAGS[label] if label else spoken_by
            body = line[match.end() :]
            if stamp is None and label is None and elements and elements[-1][0] == tag:
                elements[-1][2].append(body)
                continue
            elements.append((tag, stamp, [body]))
    if not elements:
        return ""
    lines = ["<Conversation>"]
    for tag, stamp, body in elements:
        opening = f'<{tag} at="{stamp}">' if stamp else f"<{tag}>"
        lines.append(opening + "\n".join(body) + f"</{tag}>")
    lines.append("</Conversation>")
    return "\n".join(lines)
