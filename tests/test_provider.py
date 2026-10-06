from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import httpx
import pytest
import respx

from llm_gateway import (
    PRESETS,
    CompletionRequest,
    CompletionTurn,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ScriptedProvider,
    ToolCall,
    preset_config,
    standard_message,
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
        "tools": ({"type": "function", "function": {"name": "query_data", "parameters": {}}},),
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
            "function": {"name": "query_data", "arguments": "{invalid json"},
        }
    ]
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=response))
    provider = OpenAICompatibleProvider(_config())
    try:
        turn = await provider.complete(_request())
    finally:
        await provider.aclose()

    assert turn.tool_calls == (ToolCall("call-1", "query_data", "{invalid json"),)


@respx.mock
async def test_the_reasoning_is_read_in_whichever_field_the_provider_used():
    """TG-THINK-017 — tests/brd/tg_agent_shell/telegram_history.feature"""
    details = [{"type": "reasoning.encrypted", "data": "opaque", "index": 0}]
    returned = [
        {"reasoning_content": "Read first."},
        {"reasoning": "Read first.", "reasoning_details": details},
        {},
    ]
    responses = []
    for fields in returned:
        response = _response().json()
        response["choices"][0]["message"].update(fields)
        responses.append(httpx.Response(200, json=response))
    respx.post(COMPLETIONS).mock(side_effect=responses)
    provider = OpenAICompatibleProvider(_config())
    try:
        turns = [await provider.complete(_request()) for _ in returned]
    finally:
        await provider.aclose()

    assert [dict(turn.extensions) for turn in turns] == returned


def test_a_response_goes_back_as_its_assistant_message_with_its_reasoning():
    """TG-THINK-017 — tests/brd/tg_agent_shell/telegram_history.feature"""
    details = [{"type": "reasoning.encrypted", "data": "opaque", "index": 0}]
    turn = CompletionTurn(
        "",
        (ToolCall("call-1", "query_data", "{}"),),
        extensions={"reasoning": "Read first.", "reasoning_details": details},
    )

    assert turn.as_message() == {
        "role": "assistant",
        "content": None,
        "reasoning": "Read first.",
        "reasoning_details": details,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "query_data", "arguments": "{}"},
            }
        ],
    }
    assert CompletionTurn("Hi.").as_message() == {"role": "assistant", "content": "Hi."}


@respx.mock
async def test_what_the_provider_adds_goes_back_to_it_as_it_came():
    """TG-THINK-017 — tests/brd/tg_agent_shell/telegram_history.feature"""
    signature = {"google": {"thought_signature": "opaque"}}
    response = _response().json()
    response["choices"][0]["message"].update(
        {
            "content": None,
            "reasoning_content": "Read first.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "index": 0,
                    "type": "function",
                    "function": {"name": "query_data", "arguments": "{}"},
                    "extra_content": signature,
                }
            ],
        }
    )
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=response))
    provider = OpenAICompatibleProvider(_config())
    try:
        turn = await provider.complete(_request())
    finally:
        await provider.aclose()

    message = turn.as_message()
    call = {"id": "call_1", "type": "function", "function": {"name": "query_data", "arguments": "{}"}}
    assert message["reasoning_content"] == "Read first."
    assert message["tool_calls"] == [{**call, "extra_content": signature}]
    assert standard_message(message) == {"role": "assistant", "content": None, "tool_calls": [call]}


@respx.mock
async def test_a_call_id_another_model_would_refuse_is_replaced_before_anything_keeps_it():
    ids = ["call_ok-1", "functions.card:0", "", "call_ok-1", "x" * 41]
    response = _response().json()
    response["choices"][0]["message"]["tool_calls"] = [
        {"id": call_id, "type": "function", "function": {"name": "query_data", "arguments": "{}"}}
        for call_id in ids
    ]
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=response))
    provider = OpenAICompatibleProvider(_config())
    try:
        turn = await provider.complete(_request())
    finally:
        await provider.aclose()

    kept = [call.id for call in turn.tool_calls]
    assert kept[0] == "call_ok-1"
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,40}", call_id) for call_id in kept)
    assert len(set(kept)) == len(ids)


@respx.mock
async def test_cache_breakpoints_mark_the_prompt_and_what_ends_before_the_newest_request():
    route = respx.post(COMPLETIONS).mock(return_value=_response())
    messages = (
        {"role": "system", "content": "Prompt."},
        {"role": "user", "content": "Plan this week."},
        {"role": "assistant", "content": "What matters most?"},
        {"role": "user", "content": "Health.\n[System]: Current local time: 10:00"},
        {"role": "assistant", "content": None, "tool_calls": [_CALL]},
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    )
    provider = OpenAICompatibleProvider(_config(cache_breakpoints=True))
    try:
        await provider.complete(_request(messages=messages))
    finally:
        await provider.aclose()

    sent = json.loads(route.calls[0].request.content)["messages"]
    assert [index for index, message in enumerate(sent) if isinstance(message["content"], list)] == [0, 2]
    assert sent[0]["content"] == [
        {"type": "text", "text": "Prompt.", "cache_control": {"type": "ephemeral"}}
    ]
    # The marker is on the wire only: the session's own messages stay as they were.
    assert messages[0]["content"] == "Prompt."


_CALL = {"id": "call_1", "type": "function", "function": {"name": "query_data", "arguments": "{}"}}

# What each row puts on the wire. A new row states its own here; why each value is what it
# is, model by model, is docs/LLM_GATEWAY.md.
WIRE = {
    "local": {"temperature": True, "cache": False},
    "openrouter": {"temperature": False, "cache": True},
}


def test_every_row_states_what_it_puts_on_the_wire():
    assert set(WIRE) == set(PRESETS)


@pytest.mark.parametrize("preset", sorted(PRESETS))
async def test_a_request_goes_out_as_its_row_says(preset):
    expected = WIRE[preset]
    config = preset_config(preset, api_key="test-key", model="some-model", max_output_tokens=512)
    messages = (
        {"role": "system", "content": "Prompt."},
        {"role": "user", "content": "Add a Card to buy milk."},
        {"role": "assistant", "content": None, "tool_calls": [_CALL]},
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        {"role": "assistant", "content": "Added."},
        {"role": "user", "content": "Thanks!"},
    )
    with respx.mock:
        route = respx.post(url__regex=r".*/chat/completions$").mock(return_value=_response())
        provider = OpenAICompatibleProvider(config)
        try:
            await provider.complete(_request(messages=messages, reasoning_effort="none"))
        finally:
            await provider.aclose()

    request = route.calls[0].request
    body = json.loads(request.content)
    assert str(request.url).startswith(config.base_url.rstrip("/"))
    assert body["max_tokens"] == 512
    assert ("temperature" in body) is expected["temperature"]
    assert body["reasoning_effort"] == "none"
    assert [message["role"] for message in body["messages"]] == [m["role"] for m in messages]
    marked = [index for index, message in enumerate(body["messages"]) if isinstance(message["content"], list)]
    assert marked == ([0, 4] if expected["cache"] else [])


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
async def test_a_turn_cut_off_at_the_output_limit_is_reported_and_not_asked_again():
    # A reasoning model that spent the whole output limit on its reasoning, as LM Studio
    # returns it: the same request runs out of the same limit again.
    route = respx.post(COMPLETIONS).mock(
        side_effect=[_contentless_response("length"), _response()]
    )

    provider = OpenAICompatibleProvider(_config())
    try:
        with pytest.raises(RuntimeError, match="finish_reason=length"):
            await provider.complete(_request())
    finally:
        await provider.aclose()

    assert len(route.calls) == 1


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


# What only the gateway may know: an endpoint's name or address, or a field of its wire.
PROVIDER_WORDS = (
    "openrouter",
    "lmstudio",
    "ollama",
    "anthropic",
    "reasoning_content",
    "reasoning_details",
    "cache_control",
)


def _code_words(tree: ast.AST) -> list[str]:
    """Every name and string the code uses, its docstrings left out: prose may say why."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    words: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                words.append(node.value)
        elif isinstance(node, ast.Name):
            words.append(node.id)
        elif isinstance(node, ast.Attribute):
            words.append(node.attr)
        elif isinstance(node, ast.arg | ast.keyword) and node.arg:
            words.append(node.arg)
    return words


def test_provider_knowledge_stays_in_the_gateway():
    source_root = Path(__file__).parents[1] / "src"
    leaks = sorted(
        f"{path.relative_to(source_root).as_posix()}: {term}"
        for path in source_root.rglob("*.py")
        if "llm_gateway" not in path.parts
        for word in _code_words(ast.parse(path.read_text(encoding="utf-8")))
        for term in PROVIDER_WORDS
        if term in word.lower()
    )

    assert not leaks, leaks
