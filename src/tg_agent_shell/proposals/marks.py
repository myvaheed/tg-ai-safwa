"""How a review screen marks the item it shows (PR-SCREEN-003).

A presenter hands over each field as it is and as Save leaves it, in the owner's words; this
is the one place that decides how the two read. A new item reads as it will be and one that
goes as it is, both plain. An edit strikes what goes and bolds what comes, and shows of the
rest only what tells the owner which item it is. No feature writes a diff of its own.
"""

from __future__ import annotations

import html
import re
from difflib import SequenceMatcher
from typing import Any

from .api import Effect, FieldValue, ShownField

# A value of at most this many words on both sides reads whole, struck and then bold.
SHORT_VALUE_WORDS = 3
# Words losing more than this share of their characters read as the new words alone.
REPLACED_SHARE = 0.5
# A field longer than this folds into a quote the owner opens.
LONG_TEXT_CHARS = 200
# What an empty field shown on every proposal reads as.
EMPTY_FIELD = "—"

# A word, a run of spaces, or a whole citation, so a mark never cuts `[title](card:12)` apart.
_TOKEN = re.compile(r"\[(?:[^\[\]\n]|\[[^\[\]\n]*\])*\]\([a-z_]+:[^)\s]*\)|\s+|\S+")
_MARKUP = re.compile(r"<[^>]+>")


def shown(value: Any) -> FieldValue:
    """A stored value as a screen field holds it: empty is None, a list is its items."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None or value == "" or value == [] or value == ():
        return None
    if isinstance(value, list | tuple | set):
        return tuple(str(item) for item in value)
    return str(value)


def _items(value: FieldValue) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return value
    return (value,) if value else ()


def _escaped(value: FieldValue) -> str:
    return ", ".join(html.escape(item) for item in _items(value))


def _wrapped(mark: str, text: str) -> str:
    """`text` inside `mark`, its edge spaces left outside so a mark never opens on a gap."""
    core = text.strip()
    if not core:
        return html.escape(text)
    start = text.index(core)
    return (
        f"{html.escape(text[:start])}<{mark}>{html.escape(core)}</{mark}>"
        f"{html.escape(text[start + len(core):])}"
    )


def marked(before: FieldValue, after: FieldValue) -> str:
    """One edited field: what goes struck, what comes bold, the rest plain."""
    if isinstance(before, tuple) or isinstance(after, tuple):
        old, new = _items(before), _items(after)
        return ", ".join(
            [html.escape(item) if item in new else _wrapped("s", item) for item in old]
            + [_wrapped("b", item) for item in new if item not in old]
        )
    old_text, new_text = before or "", after or ""
    if not old_text:
        return _wrapped("b", new_text)
    if not new_text:
        return _wrapped("s", old_text)
    if max(len(old_text.split()), len(new_text.split())) <= SHORT_VALUE_WORDS:
        return f"{_wrapped('s', old_text)} {_wrapped('b', new_text)}"
    old, new = _TOKEN.findall(old_text), _TOKEN.findall(new_text)
    steps = SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
    gone = sum(
        len(token)
        for step, i1, i2, _, _ in steps
        if step in {"replace", "delete"}
        for token in old[i1:i2]
        if not token.isspace()
    )
    if gone > REPLACED_SHARE * sum(len(token) for token in old if not token.isspace()):
        return _wrapped("b", new_text)
    parts: list[str] = []
    for step, i1, i2, j1, j2 in steps:
        if step == "equal":
            parts.append(html.escape("".join(old[i1:i2])))
            continue
        if i2 > i1:
            parts.append(_wrapped("s", "".join(old[i1:i2])))
        if j2 > j1:
            parts.append((" " if i2 > i1 else "") + _wrapped("b", "".join(new[j1:j2])))
    return "".join(parts)


def field_line(field: ShownField, effect: Effect) -> str | None:
    """One field as a review screen shows it, or None when it says nothing there."""
    if effect is Effect.CREATE:
        text = _escaped(field.after)
    elif effect in {Effect.ARCHIVE, Effect.DELETE}:
        text = _escaped(field.before)
    elif field.before != field.after:
        text = marked(field.before, field.after)
    elif field.anchor or field.always:
        text = _escaped(field.after)
    else:
        return None
    if not text and field.always:
        text = EMPTY_FIELD
    if not text:
        return None
    label = html.escape(field.label)
    if len(html.unescape(_MARKUP.sub("", text))) > LONG_TEXT_CHARS:
        return f"<i>{label}:</i>\n<blockquote expandable>{text}</blockquote>"
    return f"<i>{label}:</i> {text}"
