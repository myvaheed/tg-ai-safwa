"""Small model calls about one Value link: its direction once it is saved, and what of it
a Note still says once it is removed."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import Field

from llm_gateway import LlmProvider
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session

from ...foundation.method import bullets, lines
from .api import LINK

VALUE_LINK_PROMPT = (
    "You review one saved Value link.\n"
    "Decide whether it is sufficiently clear how the item serves the Value.\n\n"
    f"{LINK.definition}\n"
    f"{lines(LINK.criteria)}"
    "A Check serves a Value when it observes that the Value holds.\n"
    "Use the title, Note and Value description.\n"
    f"{lines(LINK.leaves)}\n"
    "Examples:\n"
    + bullets(tuple(
        f"{example.item}: clear={'true' if example.clear else 'false'}"
        + (f". {example.verdict}" if example.verdict else "")
        for example in LINK.examples
    ))
    + "The same link can be clear or not depending on the user's meaning.\n"
    "Call value_link_review with clear=false when the item needs the Value instead of serving it.\n"
    "Call value_link_review with clear=false when it could either serve or need the Value, "
    "and the title, Note and Value description do not say which.\n"
    "Otherwise call value_link_review with clear=true."
)


VALUE_MENTION_PROMPT = (
    "You read the Note of one item that no longer carries a Value.\n"
    "Decide whether the Note still says something about that Value.\n\n"
    "It does when it names the Value, says what the Value means, "
    "or says how the item supports it.\n"
    "It does not when it only describes the work itself.\n\n"
    'Example Value "Health":\n'
    '- "Money pays for my healthcare": mentions.\n'
    '- "Keeps me fit and sleeping well": mentions.\n'
    '- "Route along the river, 5 km": does not mention.\n'
    "Call value_mention_review with mentioned=true when the Note mentions the Value.\n"
    "Otherwise call value_mention_review with mentioned=false."
)


class ValueLinkInput(ToolInput):
    clear: bool = Field(description="It is sufficiently clear how the item serves the Value.")


class ValueMentionInput(ToolInput):
    mentioned: bool = Field(description="The Note still says something about the Value.")


VALUE_LINK_TERMINAL = TerminalTool("value_link_review", "Judge one Value link.", ValueLinkInput)
VALUE_MENTION_TERMINAL = TerminalTool(
    "value_mention_review", "Judge one Note after its Value was removed.", ValueMentionInput
)


class ValueLinkReviewer:
    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def clear(self, link: Mapping[str, Any]) -> bool:
        answer = await self._judge(VALUE_LINK_PROMPT, VALUE_LINK_TERMINAL, link)
        return answer.clear

    async def mentioned(self, link: Mapping[str, Any]) -> bool:
        answer = await self._judge(VALUE_MENTION_PROMPT, VALUE_MENTION_TERMINAL, link)
        return answer.mentioned

    async def _judge(self, prompt: str, terminal: TerminalTool, link: Mapping[str, Any]) -> Any:
        answer = await run_mini_session(
            self.provider,
            system_prompt=prompt,
            context=json.dumps(link, ensure_ascii=False),
            terminals=(terminal,),
            max_tool_calls=1,
            max_repairs=1,
            reasoning_effort="none",
        )
        return answer.payload
