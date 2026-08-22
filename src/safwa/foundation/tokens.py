"""The one token estimate every budget in Safwa is measured against.

The dialogue window, the Summary trigger and the memory file all spend the same context,
so they have to count it the same way.  It is deliberately an estimate: the exact number
belongs to a tokenizer the local and the remote provider do not share.
"""

from __future__ import annotations

from ..constants import TOKEN_CHARS_ESTIMATE


def estimate_tokens(text: str, chars_per_token: float = TOKEN_CHARS_ESTIMATE) -> int:
    return int((len(text) / max(chars_per_token, 1.0)) + 0.999)
