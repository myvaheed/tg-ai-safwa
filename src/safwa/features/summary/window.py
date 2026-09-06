"""Where the dialogue window ends: at the newest Summary.

The window itself keeps no idea of a Summary. It asks this on every read, so which
message ends it, the heading only a person reads, and the word the model reads in its
place are all decided here.
"""

from __future__ import annotations

from collections.abc import Sequence

from tg_agent_shell.foundation.kinds import MessageKind

# Written on the first message of a Summary, for the owner. `stands_for` strips it back
# off, so the model reads the words alone — one string, written and stripped from here.
SUMMARY_HEADER = "📜 Summary"

# What the model reads where the window ends. A Summary is neither the person speaking nor
# the bot answering, so it says what it is.
SUMMARY_LABEL = "[Summary]:"


class SummaryEdge:
    """`telegram_llm`'s `WindowEdge`, answered out of the chat itself."""

    def ends_window(self, message_id: int, kind: str | None, text: str) -> bool:
        return kind == MessageKind.SUMMARY.value

    def stands_for(self, parts: Sequence[str]) -> str:
        # Only the first part carries the heading, and only if the Summary was long
        # enough for Telegram to split it at all.
        first, separator, rest = parts[0].partition("\n")
        if separator and first.strip().casefold() == SUMMARY_HEADER.casefold():
            parts = [rest.strip(), *parts[1:]]
        return f"{SUMMARY_LABEL} " + "\n".join(parts)
