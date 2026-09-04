"""Writing and reading a message's kind mark, for tests that assert on marked text.

Production never needs these: it marks through `ChatHost` and reads through the window.
They live here so `MARKS` stays the one table both sides agree on.
"""

from __future__ import annotations

from tg_agent_shell.adapters.kinds import MARKS, MessageKind


def mark_kind(text: str, kind: MessageKind, *, event_id: str | None = None) -> str:
    return MARKS.write(text, kind.value, event_id=event_id)[0]


def mark_message(text: str, kind: MessageKind) -> tuple[str, str]:
    """The marked text and the event id minted with it."""
    return MARKS.write(text, kind.value)


def read_kind_mark(text: str) -> tuple[str | None, str]:
    """The kind a message was sent under, and the text without the mark."""
    kind, _event_id, visible = MARKS.read(text)
    return kind, visible
