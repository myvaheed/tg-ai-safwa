"""How a feature plugs into Safwa's Telegram interface.

An item that can be put on the screen and cited, and a screen the owner opens by name.
Each is declared by the feature that owns it, assembled by the composition root, and read
by the adapters and the `open` tool. These are wiring DTOs, so they live where all three
can reach them without any of them importing the others.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

# The bracketed half of a citation. A Diary day is labelled "04.03.2026 [6 🙂]", so one
# level of nesting is part of the shape rather than a malformed citation; the two branches
# cannot match the same character, so the alternation stays linear.
_LABEL = r"\[((?:[^\[\]\n]|\[[^\[\]\n]*\]){1,120})\]"


@dataclass(frozen=True, slots=True)
class ScreenSpec:
    """One item type the owner can be taken to, and how it reads when it is cited."""

    item_type: str
    # Looked up to answer "does this still exist"; a citation to something gone keeps its
    # words and loses its link.
    model: type[Any]
    # (message, services, item_id, *, replace) — the manual screen, buttons included.
    open: Callable[..., Awaitable[None]]
    # (session, services, item) -> the fixed compact label, or None to keep the model's words.
    label: Callable[..., Awaitable[str | None]]
    # A Sprint retro is reached by a citation and by a button, never by the `open` tool.
    ai_openable: bool = True


@dataclass(frozen=True, slots=True)
class ScreenCatalogue:
    """Every item type a feature publishes a screen for, and how one is cited.

    A citation is `[label](card:12)` in the model's prose and `?start=card-12` in the link
    built from it, so the patterns and both halves of the payload are one vocabulary and are
    derived here rather than written out anywhere else.
    """

    by_type: Mapping[str, ScreenSpec]
    citation: re.Pattern[str]
    # The same shape with any target: a citation the model aimed at something that is not
    # an id still has to leave the chat as words rather than as raw Markdown.
    markup: re.Pattern[str]
    # A deep-link start payload accepts only [A-Za-z0-9_-], so the type separator differs.
    payload_pattern: re.Pattern[str]

    @classmethod
    def of(cls, specs: tuple[ScreenSpec, ...]) -> ScreenCatalogue:
        names = "|".join(spec.item_type for spec in specs)
        return cls(
            by_type={spec.item_type: spec for spec in specs},
            citation=re.compile(_LABEL + r"\((" + names + r"):(\d{1,9})\)"),
            markup=re.compile(_LABEL + r"\((?:" + names + r"):[^)\s]{0,64}\)"),
            payload_pattern=re.compile(r"^(" + names + r")-(\d{1,9})$"),
        )

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(self.by_type)

    def payload(self, item_type: str, item_id: int) -> str:
        return f"{item_type}-{item_id}"

    def parse_payload(self, payload: str) -> tuple[str, int] | None:
        match = self.payload_pattern.fullmatch(payload.strip())
        return (match.group(1), int(match.group(2))) if match else None




@dataclass(frozen=True, slots=True)
class ScreenCommand:
    """One screen the owner opens by name: a slash command, a menu button, or both."""

    # (message, services) -> None.
    handler: Callable[..., Awaitable[None]]
    # Published to Telegram as `/<command>`; None means this screen has no command line.
    command: str | None = None
    description: str = ""
    # The `nav:<action>` a menu button carries; None means it is not in the menu.
    nav: str | None = None
    # Today is a real screen only while a Sprint is running.
    needs_sprint: bool = False


@dataclass(frozen=True, slots=True)
class TextInputFlow:
    """What happens when the owner types a value into one editor.

    The editor itself is one screen: it validates, keeps itself alive on a refusal, and
    takes the typed message out of the chat. A flow is only the three things that differ —
    which validator this field wants, what the value is written to, and which screen the
    editor gives way to afterwards.
    """

    # Matches the flow the editor recorded in its UiSession state.
    name: str
    # (state) -> the validator for this field, or None to accept any trimmed text.
    validator: Callable[..., Any]
    # (services, state, value) -> None. Writes the value and ends the editor's session.
    apply: Callable[..., Awaitable[None]]
    # (message, services, state, value) -> None. Redraws the screen the editor replaced.
    render: Callable[..., Awaitable[None]]
