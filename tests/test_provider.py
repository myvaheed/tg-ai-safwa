from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest
import respx

from llm_gateway import (
    CompletionRequest,
    CompletionTurn,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ScriptedProvider,
    ToolCall,
)

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


def _config(**overrides) -> OpenAICompatibleConfig:
    values = {
        "base_url": BASE_URL,
        "api_key": "test-key",
        "model": "openai/gpt-5.6-luna",
        "max_output_tokens": 512,
    }
    values.update(overrides)
    return OpenAICompatibleConfig(**values)


def _request(**overrides) -> CompletionRequest:
    values = {
        "messages": ({"role": "system", "content": "hi"},),
        "tools": ({"type": "function", "function": {"name": "query_safwa", "parameters": {}}},),
    }
    values.update(overrides)
    return CompletionRequest(**values)


async def _run(config: OpenAICompatibleConfig, route) -> tuple[dict, httpx.Request]:
    provider = OpenAICompatibleProvider(config)
    try:
        turn = await provider.complete(_request())
    finally:
        await provider.aclose()
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
async def test_request_reasoning_effort_overrides_the_adapter_default():
    route = respx.post(COMPLETIONS).mock(return_value=_response())
    provider = OpenAICompatibleProvider(_config(reasoning_effort="low"))
    try:
        await provider.complete(_request(reasoning_effort="high"))
    finally:
        await provider.aclose()

    assert json.loads(route.calls[0].request.content)["reasoning_effort"] == "high"


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
async def test_structured_output_uses_the_neutral_response_schema():
    route = respx.post(COMPLETIONS).mock(return_value=_response())
    provider = OpenAICompatibleProvider(_config(structured_output=True))
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    try:
        await provider.complete(_request(response_schema=schema))
    finally:
        await provider.aclose()

    response_format = json.loads(route.calls[0].request.content)["response_format"]
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == schema


@respx.mock
async def test_tool_call_keeps_invalid_arguments_json_raw():
    response = _response().json()
    response["choices"][0]["message"]["tool_calls"] = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "query_safwa", "arguments": "{invalid json"},
        }
    ]
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=response))
    provider = OpenAICompatibleProvider(_config())
    try:
        turn = await provider.complete(_request())
    finally:
        await provider.aclose()

    assert turn.tool_calls == (ToolCall("call-1", "query_safwa", "{invalid json"),)


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


def _empty_response(error: dict | None = None) -> httpx.Response:
    payload: dict = {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "openai/gpt-5.6-luna",
        "choices": [],
    }
    if error is not None:
        payload["error"] = error
    return httpx.Response(200, json=payload)


def _contentless_response(finish_reason: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "gen-1",
            "object": "chat.completion",
            "created": 0,
            "model": "openai/gpt-5.6-luna",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": ""},
                }
            ],
        },
    )


@respx.mock
async def test_an_empty_response_is_retried_once():
    route = respx.post(COMPLETIONS).mock(
        side_effect=[_empty_response({"message": "upstream stalled"}), _response()]
    )

    provider = OpenAICompatibleProvider(_config())
    try:
        turn = await provider.complete(_request())
    finally:
        await provider.aclose()

    assert turn.content == "Done."
    assert len(route.calls) == 2


@respx.mock
async def test_a_persistently_empty_response_reports_the_provider_reason():
    route = respx.post(COMPLETIONS).mock(
        side_effect=[_empty_response({"message": "upstream stalled"})] * 2
    )

    provider = OpenAICompatibleProvider(_config())
    try:
        with pytest.raises(RuntimeError, match="upstream stalled"):
            await provider.complete(_request())
    finally:
        await provider.aclose()

    assert len(route.calls) == 2


@respx.mock
async def test_a_choice_without_content_reports_its_finish_reason():
    respx.post(COMPLETIONS).mock(side_effect=[_contentless_response("length")] * 2)

    provider = OpenAICompatibleProvider(_config())
    try:
        with pytest.raises(RuntimeError, match="finish_reason=length"):
            await provider.complete(_request())
    finally:
        await provider.aclose()


@respx.mock
async def test_a_deliberate_silent_stop_is_an_answer_not_a_failure():
    route = respx.post(COMPLETIONS).mock(return_value=_contentless_response("stop"))

    provider = OpenAICompatibleProvider(_config())
    try:
        turn = await provider.complete(_request())
    finally:
        await provider.aclose()

    assert turn.content == ""
    assert turn.tool_calls == ()
    assert len(route.calls) == 1


async def test_scripted_provider_needs_no_monkeypatching():
    expected = CompletionTurn("Done.")
    provider = ScriptedProvider((expected,))
    request = _request()

    assert await provider.complete(request) is expected
    assert provider.requests == [request]
    await provider.aclose()


PACKAGE = Path(__file__).resolve().parents[1] / "src" / "llm_gateway"

# What a public name in the gateway may never be about: three are the applications built on
# it, and three are the deliveries it must not know. A completion is a one-shot effect, so a
# gateway that names a chat, a run or a database has taken on somebody else's state.
FOREIGN = ("safwa", "advisor", "agent", "telegram", "aiogram", "sqlalchemy")


def _public_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.append(node.name)
            names.extend(argument.arg for argument in node.args.args)
            names.extend(argument.arg for argument in node.args.kwonlyargs)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return [name for name in names if not name.startswith("_")]


@pytest.mark.parametrize("module", ["model.py", "provider.py"])
def test_the_vocabulary_of_the_package_belongs_to_no_application(module: str) -> None:
    foreign = [
        name
        for name in _public_names(PACKAGE / module)
        if any(word in name.lower() for word in FOREIGN)
    ]
    assert not foreign, f"llm_gateway/{module} names {foreign}"


def test_no_module_in_the_package_imports_the_application() -> None:
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}: {alias.name}"
                    for alias in node.names
                    if alias.name.split(".")[0] == "safwa"
                ]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if (node.module or "").split(".")[0] == "safwa":
                    offenders.append(f"{path.name}: {node.module}")

    assert not offenders, offenders


def test_openai_sdk_is_imported_only_by_the_gateway_adapter():
    source_root = Path(__file__).parents[1] / "src"
    importers = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.ImportFrom) and node.module == "openai" for node in ast.walk(tree)
        ):
            importers.append(path.relative_to(source_root).as_posix())

    assert importers == ["llm_gateway/openai_compatible.py"]
