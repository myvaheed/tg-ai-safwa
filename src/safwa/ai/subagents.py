"""Routed subagents: the Advisor hands its turn over, and they finish it themselves.

A routed subagent is a session of the same shape as the Advisor's — its own prompt, its own
tools, its own transcript — reading the same conversation.  What it writes is the chat
message, and what it proposes is the review screen, with nothing relayed in between.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .mini import ReadToolSpec

# Voice, language and citations are one block for every routed subagent: three copies of
# these rules would drift into three dialects of Safwa.
PERSONA = """# Safwa
You are one part of Safwa, the user's personal agile advisor.
The Advisor routed this request to you and is waiting. It writes to the user; you do the work. 
What you write goes back to it, in the user's language, and it answers from there.
- Cite any item you name as a Markdown link over its type and ID: `[Go to the market](card:12)`,
  `[Milk](check:14)`, `[Health](value:3)`, `[home](tag:7)`, `[Stale Actions](request:2)`,
  `[04.03.2026](diary:12)`. Only a real numeric ID, never one you invented.
- A mutation tool prepares a change for the user to approve; it is never already done. Never say a change is saved before its result says so.
- Tool results are authoritative and carry their own instructions. Obey the `hint` on an error and the `next` on a prepared or resolved call.
"""


@dataclass(frozen=True)
class RoutedSubagent:
    """One subagent `route(name)` can hand the turn to."""

    name: str
    # One line for the routing rules in the Advisor's prompt.
    purpose: str
    instructions: str
    read_tools: tuple[ReadToolSpec, ...] = ()
    mutation_tools: tuple[str, ...] = ()
    # Whether the board's current state belongs in its context at all.
    board_state: bool = False
    # The volatile line that goes after the dialogue, never into the cached prefix.
    clock: Callable[[], str] | None = field(default=None)

    @property
    def prompt(self) -> str:
        return f"{PERSONA}\n{self.instructions}"
