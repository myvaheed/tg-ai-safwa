"""What a turn leaves in the conversation, and the conversation as data for a reader that
did not take part in it."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from llm_gateway import REASONING_FIELDS

# The reads whose rows are stale by the next turn. The call stays in the conversation and the
# rows go: the workspace state is read fresh every turn, and a read can be made again.
CLEARED_READS = frozenset({"query_data", "call_helper"})
CLEARED_READ = {"status": "cleared", "next": "Read again if you need these rows."}

# Who each line of the owner's side belongs to.  The keys are the labels the window writes;
# the values are what a reader that did not take part sees.
CONVERSATION_TAGS = {
    "User": "User",
    "Assistant": "Advisor",
    "Summary": "Summary",
    "System": "System",
}
_CONVERSATION_LINE = re.compile(
    r"^(?:\[(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] )?"
    r"(?:\[(?P<label>User|Assistant|Summary|System)\]: )?"
)


def kept_turn(
    transcript: Sequence[Mapping[str, Any]], words: str, *, request: str | None = None
) -> tuple[dict[str, Any], ...]:
    """What a finished turn leaves in the conversation for the turns after it to read.

    The calls it made and what came back, in the order it made them, then its own words —
    the shape a model is trained on, so the next turn sees that a change is made by calling,
    not by saying so. A turn nobody asked for opens with the request that caused it.
    The reasoning between its calls stays behind: a model's own template drops the
    reasoning of every turn before the last request.
    """
    turn: list[dict[str, Any]] = [{"role": "user", "content": request}] if request else []
    for message in transcript:
        if message.get("role") == "tool" and message.get("name") in CLEARED_READS:
            message = {**message, "content": json.dumps(CLEARED_READ)}
        turn.append({key: value for key, value in message.items() if key not in REASONING_FIELDS})
    if words.strip():
        turn.append({"role": "assistant", "content": words})
    return tuple(turn)


def receipt_lines(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    """What the `route` results among these messages say was done, line by line."""
    return [
        str(line)
        for message in messages
        if message.get("role") == "tool" and message.get("name") == "route"
        for line in json.loads(str(message.get("content") or "{}")).get("did") or ()
    ]


def conversation_block(dialogue: Sequence[Mapping[str, Any]], last: int | None = None) -> str:
    """The newest `last` things said, as tagged data rather than as the reader's own turns.

    The Advisor is the assistant of this conversation and reads the roles as they are.
    Anyone routed into it is not: prose in the `assistant` slot would be a standing
    demonstration of answering in prose, which is the one thing a subagent must not do.
    Every line says whose it is instead, and none of them is the reader's own. Of an
    Advisor's calls only what `route` did is kept: a read is how it answered, not what.
    """
    elements: list[tuple[str, str | None, list[str]]] = []
    for item in dialogue:
        role = item.get("role")
        if role == "tool":
            done = receipt_lines([item])
            if done:
                elements.append(("ToolResult", None, done))
            continue
        content = item.get("content")
        if not isinstance(content, str) or (role == "assistant" and item.get("tool_calls")):
            continue
        spoken_by = "Advisor" if role == "assistant" else "User"
        for line in content.splitlines():
            match = _CONVERSATION_LINE.match(line)
            stamp, label = match.group("stamp"), match.group("label")
            tag = CONVERSATION_TAGS[label] if label else spoken_by
            body = line[match.end() :]
            if stamp is None and label is None and elements and elements[-1][0] == tag:
                elements[-1][2].append(body)
                continue
            elements.append((tag, stamp, [body]))
    if last is not None:
        elements = elements[-last:]
    if not elements:
        return ""
    lines = ["<Conversation>"]
    for tag, stamp, body in elements:
        opening = f'<{tag} at="{stamp}">' if stamp else f"<{tag}>"
        lines.append(opening + "\n".join(body) + f"</{tag}>")
    lines.append("</Conversation>")
    return "\n".join(lines)
