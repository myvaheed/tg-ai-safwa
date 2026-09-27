"""The configured provider, for real: a tool loop, the loop read back as kept, and one photo.

Opt-in with ``--live-provider``; it reads SAFWA_AI_* from `.env` and costs a few requests.
Run it once for every row of `PRESETS` you mean to use, before trusting it.
"""

from __future__ import annotations

import io
import json

import pytest

from llm_gateway import CompletionRequest, OpenAICompatibleProvider, standard_message
from safwa.bootstrap.modules import PROPOSALS
from safwa.config import Settings
from tg_agent_shell.media.library import image_part

pytestmark = pytest.mark.live_provider

LOOKUP = {
    "type": "function",
    "function": {
        "name": "lookup",
        "description": "Find the owner's Cards whose title contains the words.",
        "parameters": {
            "type": "object",
            "properties": {"words": {"type": "string"}},
            "required": ["words"],
            "additionalProperties": False,
        },
    },
}
ROWS = json.dumps({"rows": [{"id": 1, "title": "Buy milk"}]})


def _photo() -> bytes:
    image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    image.new("RGB", (64, 64), (200, 30, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _answer(provider, messages: list[dict], tools: tuple) -> None:
    """Run the loop until the model answers in words, as the agent loop does."""
    for _ in range(4):
        turn = await provider.complete(CompletionRequest(tuple(messages), tools=tools))
        messages.append(turn.as_message())
        if not turn.tool_calls:
            return
        messages.extend(
            {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": ROWS}
            for call in turn.tool_calls
        )
    pytest.fail("The model kept calling tools and never answered.")


async def test_the_configured_provider_loops_reads_its_kept_turn_and_sees_a_photo():
    settings = Settings()
    provider = OpenAICompatibleProvider(settings.ai_config())
    # The real card schema goes along: the keywords every provider must accept.
    tools = (LOOKUP, PROPOSALS.tools["card"].schema())
    messages: list[dict] = [
        {"role": "system", "content": "You keep the owner's Cards. Call lookup before you answer."},
        {"role": "user", "content": "Do I have a Card about milk?"},
    ]
    try:
        first = await provider.complete(
            CompletionRequest(tuple(messages), tools=tools, tool_choice="required")
        )
        assert first.tool_calls, "The model answered without calling a tool."
        messages.append(first.as_message())
        messages.extend(
            {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": ROWS}
            for call in first.tool_calls
        )
        # The step after a call carries what the provider added to it: Gemini 3 and
        # DeepSeek refuse it otherwise.
        await _answer(provider, messages, tools)

        # The next turn reads the last one as it is kept: standard keys only.
        kept = [standard_message(message) for message in messages]
        kept.append({"role": "user", "content": "And is there one about eggs?"})
        await _answer(provider, kept, tools)

        words = await provider.complete(
            CompletionRequest(
                (
                    {"role": "system", "content": "Say in at most five words what the photo shows."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "The photo:"},
                            image_part(_photo(), "image/jpeg"),
                        ],
                    },
                ),
                reasoning_effort="none",
            )
        )
        assert words.content.strip(), "The model returned no words for the photo."
    finally:
        await provider.aclose()
