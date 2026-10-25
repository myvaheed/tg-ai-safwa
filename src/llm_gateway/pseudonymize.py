"""Identifiers out of a request, and back into its answer.

`PseudonymizingProvider` wraps any `LlmProvider`. Before a request leaves, what a `PiiFinder`
finds in the text of each message is replaced by a placeholder such as `[PERSON_1]`; when the
answer comes back, each placeholder the request carried is replaced by what it stood for, in
the answer and in the arguments of every call. The placeholders are a function of the request
alone: a session's transcript only grows, so they keep their numbers from one step to the
next, and nothing is kept between requests but what was found in each text.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Protocol

from .model import CompletionRequest, CompletionTurn, Message
from .provider import LlmProvider

PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")
# Texts whose identifiers are remembered: a dialogue window and its reads, many times over.
FOUND_TEXTS_KEPT = 1024


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    kind: str


class PiiFinder(Protocol):
    def find(self, text: str) -> Sequence[Span]:
        """The identifiers in a text. It blocks, so it is called off the event loop."""


class PseudonymizingProvider:
    def __init__(self, inner: LlmProvider, finder: PiiFinder) -> None:
        self._inner = inner
        self._find = lru_cache(maxsize=FOUND_TEXTS_KEPT)(lambda text: tuple(finder.find(text)))

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        texts: list[str] = []

        def collect(text: str) -> str:
            texts.append(text)
            return text

        for message in request.messages:
            _map_texts(message, collect)
        found = await asyncio.to_thread(lambda: {text: self._find(text) for text in texts})
        table = _placeholders(texts, found)
        masked = tuple(
            _map_texts(message, lambda text: _mask(text, found[text], table))
            for message in request.messages
        )
        turn = await self._inner.complete(replace(request, messages=masked))
        return _restore(turn, {placeholder: surface for surface, placeholder in table.items()})

    async def aclose(self) -> None:
        await self._inner.aclose()


def _map_texts(message: Message, change: Callable[[str], str]) -> Message:
    """The message with `change` applied to each of its texts, in order.

    Safwa's own instructions are not the owner's words, and a photo is not text: both go
    as they are.
    """
    if message.get("role") == "system":
        return message
    changed = dict(message)
    content = message.get("content")
    if isinstance(content, str):
        changed["content"] = change(content)
    elif isinstance(content, list):
        changed["content"] = [
            {**part, "text": change(part["text"])} if part.get("type") == "text" else part
            for part in content
        ]
    if message.get("tool_calls"):
        changed["tool_calls"] = [
            {
                **call,
                "function": {
                    **call["function"],
                    "arguments": _map_arguments(call["function"]["arguments"], change),
                },
            }
            for call in message["tool_calls"]
        ]
    return changed


def _map_arguments(arguments: str, change: Callable[[str], str]) -> str:
    try:
        value = json.loads(arguments)
    except ValueError:
        return change(arguments)
    changed = _map_json(value, change)
    return arguments if changed == value else json.dumps(changed, ensure_ascii=False)


def _map_json(value: Any, change: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        return [_map_json(item, change) for item in value]
    if isinstance(value, dict):
        return {key: _map_json(item, change) for key, item in value.items()}
    return value


def _placeholders(texts: list[str], found: dict[str, tuple[Span, ...]]) -> dict[str, str]:
    """Each spelling found, numbered by kind in the order it first appears."""
    table: dict[str, str] = {}
    counts: dict[str, int] = {}
    for text in texts:
        for span in sorted(found[text], key=lambda span: span.start):
            surface = text[span.start : span.end]
            if surface not in table:
                counts[span.kind] = counts.get(span.kind, 0) + 1
                table[surface] = f"[{span.kind}_{counts[span.kind]}]"
    return table


def _mask(text: str, spans: tuple[Span, ...], table: dict[str, str]) -> str:
    """The text with what was found in it, and every whole-word recurrence of a spelling with
    a letter found anywhere in the request, replaced by its placeholder.

    A value of digits alone, an age, recurs as a date or a count, so it is replaced only where
    it was found.
    """
    hits = [(span.start, span.end) for span in spans]
    words = sorted((surface for surface in table if any(c.isalpha() for c in surface)), key=len)
    if words:
        recurrence = re.compile(
            rf"(?<!\w)(?:{'|'.join(map(re.escape, reversed(words)))})(?!\w)"
        )
        hits += [match.span() for match in recurrence.finditer(text)]
    out, position = [], 0
    for start, end in sorted(hits, key=lambda hit: (hit[0], -hit[1])):
        if start < position:
            continue
        out += [text[position:start], table[text[start:end]]]
        position = end
    out.append(text[position:])
    return "".join(out)


def _restore(turn: CompletionTurn, surfaces: dict[str, str]) -> CompletionTurn:
    """The answer with each placeholder the request carried put back; any other stays."""

    def plain(match: re.Match[str]) -> str:
        return surfaces.get(match.group(), match.group())

    def in_json(match: re.Match[str]) -> str:
        surface = surfaces.get(match.group())
        return match.group() if surface is None else json.dumps(surface, ensure_ascii=False)[1:-1]

    return replace(
        turn,
        content=PLACEHOLDER.sub(plain, turn.content),
        tool_calls=tuple(
            replace(call, arguments_json=PLACEHOLDER.sub(in_json, call.arguments_json))
            for call in turn.tool_calls
        ),
    )
