from __future__ import annotations

import json

import httpx
import pytest
import respx

from safwa.ai.provider import OpenAICompatibleProvider, ProviderConfig

BASE_URL = "https://openrouter.test/api/v1"
COMPLETIONS = f"{BASE_URL}/chat/completions"


def _response(usage: dict | None = None) -> httpx.Response:
    payload = {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "openai/gpt-5.6-luna",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Done."},
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return httpx.Response(200, json=payload)


def _config(**overrides) -> ProviderConfig:
    values = {
        "base_url": BASE_URL,
        "api_key": "test-key",
        "model": "openai/gpt-5.6-luna",
        "max_output_tokens": 512,
    }
    values.update(overrides)
    return ProviderConfig(**values)


async def _run(config: ProviderConfig, route) -> tuple[dict, httpx.Request]:
    provider = OpenAICompatibleProvider(config)
    try:
        turn = await provider.complete_turn(
            [{"role": "system", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "query_safwa", "parameters": {}}}],
        )
    finally:
        await provider.close()
    request = route.calls[0].request
    return {"turn": turn, "body": json.loads(request.content)}, request


@respx.mock
async def test_temperature_omitted_for_models_that_reject_it():
    route = respx.post(COMPLETIONS).mock(return_value=_response())

    result, _ = await _run(_config(send_temperature=False), route)

    assert "temperature" not in result["body"]
    assert result["body"]["max_tokens"] == 512
    assert result["body"]["tool_choice"] == "auto"
    assert result["body"]["stream"] is False


@respx.mock
async def test_temperature_and_reasoning_effort_sent_when_configured():
    route = respx.post(COMPLETIONS).mock(return_value=_response())

    result, _ = await _run(_config(send_temperature=True, reasoning_effort="low"), route)

    assert result["body"]["temperature"] == 0.2
    assert result["body"]["reasoning_effort"] == "low"


@respx.mock
async def test_attribution_headers_are_sent():
    route = respx.post(COMPLETIONS).mock(return_value=_response())

    _, request = await _run(
        _config(default_headers=(("HTTP-Referer", "https://safwa.test"), ("X-Title", "Safwa"))),
        route,
    )

    assert request.headers["HTTP-Referer"] == "https://safwa.test"
    assert request.headers["X-Title"] == "Safwa"


@respx.mock
async def test_usage_includes_openrouter_cache_fields():
    route = respx.post(COMPLETIONS).mock(
        return_value=_response(
            {
                "prompt_tokens": 10_339,
                "completion_tokens": 60,
                "total_tokens": 10_399,
                "cost": 0.0012,
                "prompt_tokens_details": {"cached_tokens": 10_318, "cache_write_tokens": 21},
            }
        )
    )

    result, _ = await _run(_config(), route)

    usage = result["turn"].usage
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens) == (10_339, 60)
    assert (usage.cached_tokens, usage.cache_write_tokens) == (10_318, 21)
    assert usage.cost == pytest.approx(0.0012)


@respx.mock
async def test_usage_tolerates_a_server_that_reports_no_cache_fields():
    route = respx.post(COMPLETIONS).mock(
        return_value=_response({"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15})
    )

    result, _ = await _run(_config(), route)

    usage = result["turn"].usage
    assert usage is not None
    assert (usage.cached_tokens, usage.cache_write_tokens, usage.cost) == (0, 0, None)
