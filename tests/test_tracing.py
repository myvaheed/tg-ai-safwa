from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from openai import InternalServerError
from openinference.instrumentation.openai import OpenAIInstrumentor
from openinference.semconv.trace import SpanAttributes
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NoOpTracerProvider, StatusCode
from pydantic import BaseModel, field_serializer
from test_agent_runtime import _runtime, _turn

from agent_runtime import tracing
from llm_gateway import (
    CompletionTurn,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ScriptedProvider,
    ToolCall,
)
from tg_agent_shell.ai.mini import ReadToolSpec, TerminalTool, run_mini_session

URL = "https://llm.test/v1/chat/completions"


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("agent_runtime"))
    instrumentor = OpenAIInstrumentor()
    instrumentor.instrument(tracer_provider=provider)
    try:
        yield exporter
    finally:
        instrumentor.uninstrument()
        provider.shutdown()


def _provider():
    return OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://llm.test/v1",
            api_key="secret-api-key",
            model="test-model",
            max_retries=0,
        )
    )


def _response(name: str | None = None, arguments: str = "{}") -> httpx.Response:
    message = {"role": "assistant", "content": None if name else "Done."}
    if name:
        message["tool_calls"] = [
            {
                "id": f"call-{name}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ]
    return httpx.Response(
        200,
        json={
            "id": "response-1",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {"index": 0, "finish_reason": "tool_calls" if name else "stop", "message": message}
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 4},
            },
        },
    )


@respx.mock
async def test_llm_tools_and_routed_agents_share_a_trace(spans):
    respx.post(URL).mock(
        side_effect=[_response("route"), _response("read"), _response(), _response()]
    )
    provider = _provider()
    runtime, tools, _ = _runtime(provider)
    try:
        outcome = await runtime.handle([{"role": "user", "content": "Read my notes."}])
    finally:
        await provider.aclose()

    assert outcome.message == "Done."
    assert tools.ran == ["read"]
    finished = spans.get_finished_spans()
    assert len({span.context.trace_id for span in finished}) == 1
    by_name = {span.name: span for span in finished}
    advisor, writer = by_name["advisor"], by_name["writer"]
    assert advisor.parent is None
    assert writer.parent.span_id == by_name["route"].context.span_id
    assert by_name["read"].parent.span_id == writer.context.span_id
    assert json.loads(by_name["read"].attributes[SpanAttributes.OUTPUT_VALUE]) == {"rows": []}
    assert by_name["route"].attributes[SpanAttributes.TOOL_ID] == "call-route"
    generations = [
        span
        for span in finished
        if span.attributes[SpanAttributes.OPENINFERENCE_SPAN_KIND] == "LLM"
    ]
    assert len(generations) == 4
    assert {span.parent.span_id for span in generations} == {
        advisor.context.span_id,
        writer.context.span_id,
    }
    for generation in generations:
        assert generation.attributes[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] == 12
        assert generation.attributes[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] == 3
        assert generation.attributes[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] == 15
        assert generation.attributes[SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_READ] == 4
        assert generation.end_time >= generation.start_time
        assert "secret-api-key" not in str(generation.attributes)


@respx.mock
async def test_provider_failure_is_visible_without_changing_runtime_failure_handling(spans):
    respx.post(URL).mock(
        return_value=httpx.Response(500, json={"error": {"message": "Unavailable"}})
    )
    provider = _provider()
    runtime, _, store = _runtime(provider)
    try:
        with pytest.raises(InternalServerError):
            await runtime.handle([{"role": "user", "content": "Read."}])
    finally:
        await provider.aclose()

    assert store.status(1).value == "failed"
    finished = spans.get_finished_spans()
    assert len(finished) == 2
    assert all(span.status.status_code is StatusCode.ERROR for span in finished)
    assert all(any(event.name == "exception" for event in span.events) for span in finished)


async def test_refused_tools_are_recorded_as_errors(spans):
    runtime, _, _ = _runtime(ScriptedProvider([_turn("unavailable"), CompletionTurn("Done.")]))
    assert (await runtime.handle([])).message == "Done."
    tool = next(span for span in spans.get_finished_spans() if span.name == "unavailable")
    assert tool.status.status_code is StatusCode.ERROR
    assert json.loads(tool.attributes[SpanAttributes.OUTPUT_VALUE])["code"] == "tool_not_available"


@respx.mock
async def test_mini_session_records_reads_validation_repairs_and_terminal_results(spans):
    class Answer(BaseModel):
        text: str

    async def read(call):
        return {"rows": ["A note"]}

    respx.post(URL).mock(
        side_effect=[
            _response("answer", "{invalid"),
            _response("read"),
            _response("answer", '{"text":"A note"}'),
        ]
    )
    provider = _provider()
    try:
        result = await run_mini_session(
            provider,
            system_prompt="Read and answer.",
            context="Find a note.",
            terminals=(TerminalTool("answer", "Answer.", Answer),),
            read_tools=(
                ReadToolSpec(
                    {"type": "function", "function": {"name": "read", "parameters": {}}}, read
                ),
            ),
            max_tool_calls=4,
        )
    finally:
        await provider.aclose()

    assert result.payload.text == "A note"
    finished = spans.get_finished_spans()
    assert len({span.context.trace_id for span in finished}) == 1
    root = next(span for span in finished if span.parent is None)
    assert root.name == "mini_session:answer"
    tools = [
        span
        for span in finished
        if span.attributes[SpanAttributes.OPENINFERENCE_SPAN_KIND] == "TOOL"
    ]
    assert [span.name for span in tools] == ["answer", "read", "answer"]
    assert tools[0].status.status_code is StatusCode.ERROR
    assert json.loads(tools[-1].attributes[SpanAttributes.OUTPUT_VALUE]) == {"text": "A note"}
    assert json.loads(root.attributes[SpanAttributes.OUTPUT_VALUE])["name"] == "answer"


async def test_cancelled_tool_is_marked_and_cancellation_propagates(spans):
    with pytest.raises(asyncio.CancelledError):
        with tracing.tool_span(ToolCall("call-cancel", "read", "{}")):
            raise asyncio.CancelledError
    span = spans.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "Cancelled"


async def test_disabled_tracing_keeps_the_existing_turn_and_produces_no_spans(spans, monkeypatch):
    monkeypatch.setattr(tracing, "_tracer", NoOpTracerProvider().get_tracer("agent_runtime"))
    runtime, tools, _ = _runtime(ScriptedProvider([_turn("read"), CompletionTurn("Done.")]))
    assert (await runtime.handle([])).message == "Done."
    assert tools.ran == ["read"]
    assert spans.get_finished_spans() == ()


async def test_disabled_tracing_does_not_serialize_terminal_payloads(spans, monkeypatch):
    class Answer(BaseModel):
        text: str

        @field_serializer("text")
        def serialize_text(self, value):
            raise ValueError("This payload must be returned without serialization")

    monkeypatch.setattr(tracing, "_tracer", NoOpTracerProvider().get_tracer("agent_runtime"))
    provider = ScriptedProvider(
        [CompletionTurn("", (ToolCall("call-answer", "answer", '{"text":"Done."}'),))]
    )
    result = await run_mini_session(
        provider,
        system_prompt="Answer.",
        context="Hello.",
        terminals=(TerminalTool("answer", "Answer.", Answer),),
        max_tool_calls=1,
    )
    assert result.payload.text == "Done."
    assert spans.get_finished_spans() == ()
