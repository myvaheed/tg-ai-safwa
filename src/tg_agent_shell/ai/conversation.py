"""What a turn leaves in the conversation, and the conversation as data for a reader that
did not take part in it."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent_runtime import SYSTEM_CLOSE, SYSTEM_OPEN, routed_answers, system_text
from llm_gateway import standard_message

# The shell's reads whose rows are stale by the next turn, beside the application's own read
# tools. The call stays in the conversation and the rows go: the workspace state is read fresh
# every turn, and a read can be made again.
CLEARED_READS = frozenset({"query_data", "call_helper"})
CLEARED_READ = {"status": "cleared", "note": "The rows were not kept: a new read shows them."}

# What a tool result tells the model to do at the step that got it. Read back in a later turn
# it would be obeyed again, so a kept turn leaves it behind.
STEP_ORDERS = ("next", "hint", "notice")

# Who each line of the owner's side belongs to.  The keys are the labels the window writes;
# the values are what a reader that did not take part sees.  What the interface said is not a
# label but a tagged block, which may run over several lines.
CONVERSATION_TAGS = {
    "User": "User",
    "Assistant": "Advisor",
    "Summary": "Summary",
}
# What heads the conversation for a reader that did not take part in it: a routed subagent,
# and a short session that prepares its call.
CONVERSATION_SO_FAR = (
    "The conversation so far, newest last. None of it is yours: read it for what the owner "
    "wants changed. `<quote>` before the owner's words holds the start of the message they "
    "reply to."
)
_CONVERSATION_LINE = re.compile(
    r"^(?:\[(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] )?"
    r"(?:\[(?P<label>User|Assistant|Summary)\]: )?"
)


def kept_turn(
    transcript: Sequence[Mapping[str, Any]],
    words: str,
    *,
    request: str | None = None,
    forwarded: str | None = None,
    reads: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], ...]:
    """What a finished turn leaves in the conversation for the turns after it to read.

    The calls it made and what came back, in the order it made them, then its own words —
    the shape a model is trained on, so the next turn sees that a change is made by calling,
    not by saying so. A turn nobody asked for opens with the request that caused it.
    Only the standard chat keys are kept: the reasoning between its calls, and anything
    else its provider added, stays behind, so the next turn reads the same whichever model
    answers it. Words the turn `forwarded` are its own words now, so they leave the receipt
    that carried them and the conversation holds them once. A read, the shell's or one of
    the application's `reads`, keeps its call and loses its rows. No result keeps its
    `STEP_ORDERS`: they were for the step that got them.
    """
    turn: list[dict[str, Any]] = [{"role": "user", "content": request}] if request else []
    moved = routed_answers(transcript)[forwarded][0] if forwarded else None
    for index, message in enumerate(transcript):
        if message.get("role") == "tool" and message.get("name") in CLEARED_READS | reads:
            message = {**message, "content": json.dumps(CLEARED_READ)}
        elif message.get("role") == "tool":
            message = _without_orders(message, ("text",) if index == moved else ())
        turn.append(standard_message(message))
    if words.strip():
        turn.append({"role": "assistant", "content": words})
    return tuple(turn)


def _without_orders(message: Mapping[str, Any], also: tuple[str, ...]) -> Mapping[str, Any]:
    """A tool result without its step orders and the `also` keys; one with none is unchanged."""
    try:
        result = json.loads(str(message.get("content") or ""))
    except json.JSONDecodeError:
        return message
    if not isinstance(result, dict) or not any(key in result for key in (*STEP_ORDERS, *also)):
        return message
    kept = {key: value for key, value in result.items() if key not in (*STEP_ORDERS, *also)}
    return {**message, "content": json.dumps(kept, ensure_ascii=False)}


def receipt_lines(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    """What the `route` results among these messages say was done, line by line."""
    return [
        str(line)
        for message in messages
        if message.get("role") == "tool" and message.get("name") == "route"
        for line in json.loads(str(message.get("content") or "{}")).get("did") or ()
    ]


def last_exchanges(
    dialogue: Sequence[Mapping[str, Any]], count: int
) -> Sequence[Mapping[str, Any]]:
    """The newest `count` exchanges: each of the owner's turns, and what answered it."""
    starts = [index for index, message in enumerate(dialogue) if message.get("role") == "user"]
    return dialogue[starts[-count] :] if len(starts) >= count else dialogue


def conversation_note(conversation: str) -> str:
    """A `conversation_block` as a block of the interface's, under the words that say what it
    is."""
    return system_text(f"{CONVERSATION_SO_FAR}\n{conversation}")


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
        open_system = False
        for line in content.splitlines():
            if open_system:
                open_system = not line.endswith(SYSTEM_CLOSE)
                elements[-1][2].append(line.removesuffix(SYSTEM_CLOSE))
                continue
            match = _CONVERSATION_LINE.match(line)
            stamp, label = match.group("stamp"), match.group("label")
            body = line[match.end() :]
            if label is None and body.startswith(SYSTEM_OPEN):
                body = body.removeprefix(SYSTEM_OPEN)
                open_system = not body.endswith(SYSTEM_CLOSE)
                elements.append(("System", stamp, [body.removesuffix(SYSTEM_CLOSE)]))
                continue
            tag = CONVERSATION_TAGS[label] if label else spoken_by
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
