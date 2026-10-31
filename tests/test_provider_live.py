"""The configured provider, for real: a tool loop, the loop read back as kept, one photo, and
an answer made of placeholders.

Opt-in with ``--live-provider``; it reads SAFWA_AI_* from `.env` and costs a few requests. The
placeholders also need ``--pii-model``, which downloads the PII finder's model. Run it once for
every row of `PRESETS` you mean to use, before trusting it.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from llm_gateway import (
    CompletionRequest,
    OpenAICompatibleProvider,
    PseudonymizingProvider,
    standard_message,
)
from llm_gateway.horizon import HorizonPiiFinder
from safwa.bootstrap.modules import BIRTH_WORDS, PII_KEPT, PROPOSALS
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
    # The real action schema goes along: the keywords every provider must accept.
    tools = (LOOKUP, PROPOSALS.tools["action"].schema())
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


class Recording:
    """The configured provider, keeping what was sent to it."""

    def __init__(self, inner: OpenAICompatibleProvider) -> None:
        self.inner = inner
        self.sent: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest):
        self.sent.append(request)
        return await self.inner.complete(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


@pytest.mark.pii_model
async def test_the_configured_model_keeps_the_placeholders_and_the_owner_reads_the_words():
    """AG-PRIVACY-077 — tests/brd/tg_agent_shell/agents.feature"""
    recording = Recording(OpenAICompatibleProvider(Settings().ai_config()))
    finder = HorizonPiiFinder.load(
        Path(__file__).parents[1] / "data" / "models", kept=PII_KEPT, birth_words=BIRTH_WORDS
    )
    provider = PseudonymizingProvider(recording, finder)
    request = "Позвонить Ольге Смирновой по номеру +7 916 123-45-67 до пятницы."
    try:
        turn = await provider.complete(
            CompletionRequest(
                (
                    {
                        "role": "system",
                        "content": "Повтори просьбу одной фразой. Имена и номера пиши как в ней.",
                    },
                    {"role": "user", "content": request},
                ),
                reasoning_effort="none",
            )
        )
    finally:
        await provider.aclose()

    assert recording.sent[0].messages[1]["content"] == (
        "Позвонить [PERSON_1] по номеру [PHONE_1] до пятницы."
    )
    assert "[PERSON_" not in turn.content and "[PHONE_" not in turn.content, turn.content
    assert "+7 916 123-45-67" in turn.content, turn.content
