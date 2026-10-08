"""OpenInference spans for the application's own agents and tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from llm_gateway import ToolCall

_tracer = trace.get_tracer("agent_runtime")


@contextmanager
def operation(
    name: str, kind: str, inputs: Any, attributes: Mapping[str, Any] | None = None
) -> Iterator[Span]:
    # Without a registered provider these are no-op spans; don't serialize the prompts.
    with _tracer.start_as_current_span(
        name,
        attributes={SpanAttributes.OPENINFERENCE_SPAN_KIND: kind, **(attributes or {})},
    ) as span:
        if span.is_recording():
            span.set_attribute(
                SpanAttributes.INPUT_VALUE,
                inputs
                if isinstance(inputs, str)
                else json.dumps(inputs, ensure_ascii=False, default=str),
            )
            span.set_attribute(
                SpanAttributes.INPUT_MIME_TYPE,
                "text/plain" if isinstance(inputs, str) else "application/json",
            )
        try:
            yield span
        except asyncio.CancelledError:
            span.set_status(Status(StatusCode.ERROR, "Cancelled"))
            raise


def tool_span(call: ToolCall):
    return operation(
        call.name,
        "TOOL",
        call.arguments_json,
        {
            SpanAttributes.TOOL_NAME: call.name,
            SpanAttributes.TOOL_ID: call.id,
            SpanAttributes.TOOL_PARAMETERS: call.arguments_json,
        },
    )


def record_output(span: Span, result: Any) -> None:
    if not span.is_recording():
        return
    span.set_attribute(
        SpanAttributes.OUTPUT_VALUE, json.dumps(result, ensure_ascii=False, default=str)
    )
    span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, "application/json")
    if isinstance(result, Mapping) and result.get("status") == "error":
        span.set_status(Status(StatusCode.ERROR, str(result.get("error") or result.get("code"))))
