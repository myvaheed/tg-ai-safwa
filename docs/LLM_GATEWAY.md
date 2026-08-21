# `llm_gateway`

`llm_gateway` is the reusable, stateless boundary for one LLM completion.  It does not import
Safwa, SQLAlchemy, Pydantic, Telegram libraries, settings, or application models.

Applications depend on `LlmProvider`: `complete(CompletionRequest)` returns a
`CompletionTurn`, and `aclose()` releases provider resources.  A request contains neutral
messages, tool specifications, tool choice, a response schema, and temperature/reasoning hints.
`ToolCall.arguments_json` remains raw so that the tool owner can validate it and issue a useful
repair message.

`OpenAICompatibleProvider` is the current adapter.  Its configuration owns endpoint, key, model,
timeouts, headers, and provider dialect options.  It normalizes usage—including cache fields—and
retries empty responses exactly as specified by the adapter configuration.

Tests should use `ScriptedProvider` when they need deterministic turns.  It records received
`CompletionRequest` values and does not require monkeypatching provider internals.
