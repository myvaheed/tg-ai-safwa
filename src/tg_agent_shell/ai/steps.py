"""What the turn being answered is doing, one line per step, for whoever shows it (AG-TURN-053).

The turn that shows its steps listens; whatever takes a step of it says so with `announce`,
wherever it sits in the chain. A turn nobody listens to — a Cue's, background work — hears
nothing, so the sessions and the checks say every step without asking who is there.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from agent_runtime import log_preview

Listener = Callable[[str], Awaitable[None]]

# The most of an error one step line repeats.
STEP_ERROR_CHARS = 120

_listener: ContextVar[Listener | None] = ContextVar("turn_steps", default=None)


@contextmanager
def listening(listener: Listener) -> Iterator[None]:
    """Hand every step taken inside this block to `listener`, in order."""
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)


async def announce(line: str) -> None:
    listener = _listener.get()
    if listener is not None:
        await listener(line)


def asking_line(kind: str, messages: Sequence[Mapping[str, Any]]) -> str:
    """A session about to ask the model, by what it is answering: nothing new, an error,
    or the results of its calls."""
    who = _who(kind)
    results = _newest_results(messages)
    if not results:
        return f"{who} is thinking."
    errors = [result["error"] for _, result in results if isinstance(result.get("error"), str)]
    if errors:
        return f"{who} retries: {log_preview(errors[0], STEP_ERROR_CHARS)}"
    after = dict.fromkeys(
        _who(str(result["subagent"])) if "subagent" in result else name
        for name, result in results
    )
    return f"{who} continues after {', '.join(after)}."


def preparing_line(kind: str, count: int) -> str:
    return f"{_who(kind)} is preparing {count} change{'' if count == 1 else 's'}."


def checking_line(title: str) -> str:
    return f"Checking: {title}."


def _who(kind: str) -> str:
    return kind.replace("_", " ").capitalize()


def _newest_results(messages: Sequence[Mapping[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """The tool results after the session's last response, each with its tool's name."""
    results: list[tuple[str, dict[str, Any]]] = []
    for message in reversed(messages):
        if message.get("role") != "tool":
            break
        try:
            result = json.loads(str(message.get("content") or ""))
        except json.JSONDecodeError:
            result = None
        results.append((str(message.get("name") or ""), result if isinstance(result, dict) else {}))
    return results[::-1]
