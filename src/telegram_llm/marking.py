"""The label a bot message carries about itself.

Telegram carries the message text and nothing beside it, and a stored table of labels goes
out of step with the chat the moment either is edited or rebuilt. So the label is written
into the message text itself, as characters no reader sees, and the chat stays the whole
record.

Two things are written: what kind of message this is, and an identifier that survives
editing it, so a screen rewritten in place is still the same message rather than a second
one. What the kinds are, and what each of them means, is the host's to say — this only
carries them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from uuid import uuid4

_SENTINEL = "⁠"
_DIGITS = ("​", "‌")
_KIND_WIDTH = 5
_EVENT_WIDTH = 128
_MARK_RE = re.compile(
    f"{_SENTINEL}"
    f"(?P<kind>[{''.join(_DIGITS)}]{{{_KIND_WIDTH}}})"
    f"(?P<event>[{''.join(_DIGITS)}]{{{_EVENT_WIDTH}}})$"
)


def _digits(value: int, width: int) -> str:
    return "".join(_DIGITS[(value >> shift) & 1] for shift in reversed(range(width)))


class KindMarks:
    """Writes and reads the invisible mark, over one host's kinds.

    ``codes`` maps each kind to its number and is **append-only**: a number that has been
    written into a real chat must never be given to another kind, or messages already sent
    change meaning. A kind with no number cannot be marked, which is the same as saying a
    message of that kind is off the record.
    """

    def __init__(self, codes: Mapping[str, int]) -> None:
        self.codes = dict(codes)
        self._by_code = {code: kind for kind, code in self.codes.items()}

    def write(self, text: str, kind: str, *, event_id: str | None = None) -> tuple[str, str]:
        """Append the mark, and return the marked text with the identifier it carries."""
        event_id = event_id or uuid4().hex
        marker = (
            _SENTINEL
            + _digits(self.codes[kind], _KIND_WIDTH)
            + _digits(int(event_id, 16), _EVENT_WIDTH)
        )
        return f"{text}{marker}", event_id

    def read(self, text: str) -> tuple[str | None, str | None, str]:
        """Split a message into its kind, its identifier, and what the reader sees."""
        match = _MARK_RE.search(text)
        if match is None:
            return None, None, text
        code = 0
        for digit in match.group("kind"):
            code = code * 2 + _DIGITS.index(digit)
        event = 0
        for digit in match.group("event"):
            event = event * 2 + _DIGITS.index(digit)
        return self._by_code.get(code), f"{event:032x}", text[: match.start()]
