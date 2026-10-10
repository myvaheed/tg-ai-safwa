"""One small model call about the direction of one saved Value link."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import Field

from llm_gateway import LlmProvider
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session

VALUE_LINK_PROMPT = (
    "You review one saved Value link.\n"
    "Decide whether it is sufficiently clear how the item supports the Value.\n\n"
    "Support means that achieving the Goal or doing the Action maintains or strengthens the Value.\n"
    "The Value is what the work is for, not what is needed to do the work.\n"
    "A Check observes whether such support holds.\n\n"
    'Example Goal: "Earn $4k".\n'
    "- Freedom: VALID — obviously, money gives more choices.\n"
    "- Health: VALID IF money pays for healthcare.\n"
    "- Health: INVALID IF health is needed to earn money.\n"
    "- Perseverance: INVALID by itself — earning money does not strengthen Perseverance.\n"
    "The same link can be VALID or INVALID depending on the user's meaning.\n"
    "Use the title, Note and Value description.\n"
    "An obvious benefit needs no written explanation.\n"
    "Call value_link_review with clear=true if support is sufficiently clear.\n"
    "Call value_link_review with clear=false if support is unclear or the meaning is reversed."
)


class ValueLinkInput(ToolInput):
    clear: bool = Field(description="It is sufficiently clear how the item supports the Value.")


VALUE_LINK_TERMINAL = TerminalTool("value_link_review", "Judge one Value link.", ValueLinkInput)


class ValueLinkReviewer:
    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def clear(self, link: Mapping[str, Any]) -> bool:
        answer = await run_mini_session(
            self.provider,
            system_prompt=VALUE_LINK_PROMPT,
            context=json.dumps(link, ensure_ascii=False),
            terminals=(VALUE_LINK_TERMINAL,),
            max_tool_calls=1,
            max_repairs=0,
            reasoning_effort="none",
        )
        return answer.payload.clear
