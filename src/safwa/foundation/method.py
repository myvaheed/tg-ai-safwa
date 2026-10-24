"""The shape the method takes for one item kind, and the ways it is rendered.

Each feature words the method for its own items as one `Method`: the check on a new item
reads its criteria and examples, every prompt that writes items reads its definition and how
to write one, and Compass reads the whole of it on demand. Two copies of a criterion would
drift into two methods.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Example:
    """One item as a check reads it, and what the check answers about it."""

    item: str
    clear: bool
    # What it is, when it is not clear; why, when a clear one needs saying.
    verdict: str = ""
    # The one question that follows the verdict.
    ask: str = ""


@dataclass(frozen=True, slots=True)
class Method:
    name: str
    # What the item is, one line.
    definition: str
    # Each completes "A clear <name>:".
    criteria: tuple[str, ...]
    # What a check leaves to the owner.
    leaves: tuple[str, ...]
    # How whoever writes the item writes it.
    writing: tuple[str, ...]
    examples: tuple[Example, ...]


def bullets(lines: tuple[str, ...]) -> str:
    return "".join(f"- {line}\n" for line in lines)


def lines(texts: tuple[str, ...]) -> str:
    return "".join(f"{text}\n" for text in texts)


def review_examples(method: Method) -> str:
    """The examples as a check with a clear and an unclear ending reads them."""
    return bullets(tuple(
        f"{example.item}: clear()"
        if example.clear
        else f'{example.item}: unclear("{example.verdict} {example.ask}")'
        for example in method.examples
    ))


def review_prompt(method: Method, reads: str) -> str:
    """The prompt of the check on one new item: `reads` is what it is handed."""
    return (
        f"You review one new {method.name}: {reads}.\n"
        f"{method.definition}\n\n"
        f"A clear {method.name}:\n"
        f"{bullets(method.criteria)}"
        f"{lines(method.leaves)}\n"
        "Examples:\n"
        f"{review_examples(method)}"
        "Call clear when every point holds.\n"
        "Otherwise call unclear with what is unclear and one advice."
    )


def items(*methods: Method) -> str:
    """What every prompt that writes these items is told about them."""
    return "".join(bullets((method.definition, *method.writing)) for method in methods)


def explanation(method: Method) -> str:
    """The whole of one method, for whoever explains it to the user."""
    examples = tuple(
        f"{example.item}: clear." + (f" {example.verdict}" if example.verdict else "")
        if example.clear
        else f"{example.item}: {example.verdict}"
        for example in method.examples
    )
    return (
        f"{method.definition}\n"
        f"A clear {method.name}:\n{bullets(method.criteria)}"
        f"{lines(method.leaves)}"
        + (f"How to write one:\n{bullets(method.writing)}" if method.writing else "")
        + f"Examples:\n{bullets(examples)}"
    )
