"""Items in the order of the conversation a session reads: the closest to its end first.

The end of that conversation orders them by words and by meaning, so a cut keeps what the owner
is talking about. The reminder subagent and the workspace mutator are both handed items this
way.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping

from tg_agent_shell.ai.sql import Ranking

from .tokens import TOKEN_CHARS_ESTIMATE

# How much of the conversation's end orders them; the text model reads up to 2048 tokens.
RANKED_BY_TOKENS = 512
# Who said each line of the conversation, which is no word of what it is about.
_TAG = re.compile(r"</?[A-Za-z]+>")

Key = tuple[str, int]


async def closeness_to(
    ranking: Ranking | None, conversation: str, views: Collection[str]
) -> Mapping[Key, float]:
    """How close each item is to the conversation's newest RANKED_BY_TOKENS tokens; an item
    close by neither words nor meaning is absent."""
    words = _TAG.sub(" ", conversation)[-int(RANKED_BY_TOKENS * TOKEN_CHARS_ESTIMATE) :].strip()
    if ranking is None or not words:
        return {}
    return await ranking.relevance(words, views)


def closest(lines: list[tuple[Key, str]], closeness: Mapping[Key, float], most: int) -> list[str]:
    """The `most` lines, the closest first and the rest in their own order after."""
    ordered = sorted(lines, key=lambda line: (line[0] not in closeness, -closeness.get(line[0], 0.0)))
    return [line for _key, line in ordered[:most]]
