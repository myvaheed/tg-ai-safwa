# Phoenix diagnostics

Phoenix records model requests and responses, tool arguments and results, latency, exceptions,
and the token usage reported by the provider. It runs locally, separately from Safwa. The bot
needs only the tracing SDK and OpenAI-compatible client instrumentation; the server runs in its
own environment so its dependencies and pytest plugin do not enter Safwa's environment.

## Run on Windows

From the repository root, start Phoenix in a Windows Terminal tab:

```powershell
rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File scripts/phoenix.ps1
```

The script installs the pinned Phoenix server on first use, binds it to `127.0.0.1:6006`, and
keeps its database and logs in `data/phoenix/` (ignored by Git). Leave the terminal running;
Ctrl+C stops the server. Open [Phoenix](http://localhost:6006).

Enable tracing in Safwa's `.env`:

```dotenv
SAFWA_PHOENIX_ENABLED=true
SAFWA_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces
```

In another terminal, install the updated bot dependencies and start Safwa as usual:

```powershell
rtk proxy uv sync --extra dev
rtk proxy uv run safwa
```

Send the bot a request, then open the **safwa-&lt;model&gt;** project in Phoenix. Its name uses the
full `SAFWA_AI_MODEL` value: for example, `openai/gpt-5.6-luna` creates
`safwa-openai/gpt-5.6-luna`. Model spans show the actual messages, tool schemas, parameters,
responses and input/output tokens. Tool spans show their
call IDs, arguments, results and errors. Cached tokens are shown when the endpoint reports them.
Token counts come from the endpoint; missing usage is not a measured zero. Phoenix can estimate
cost for models it knows, but local and custom model names may have no price.

## Trace boundaries

Each active stretch of an agent session has an `AGENT` span. A routed subagent nests under its
`route` tool, so model calls and tool executions share the same trace. Mini-sessions have a
`CHAIN` span named after their terminal tools; their reads and validation repairs are traced too.
Other direct model calls, including summaries and image descriptions, are captured by the same
client instrumentation and appear as individual traces when there is no enclosing agent span.

A Save/Discard or restart begins a new trace for the resumed stretch; `session.id` and
`agent.run_id` identify the same agent session across stretches. `agent.parent_run_id` identifies
its caller. Time spent waiting for the owner is outside the active trace.

Tracing is off by default. When enabled, prompts and tool results are stored in the local Phoenix
database, including the personal data supplied to the model. Trace export runs in background
batches and is flushed on normal Safwa shutdown. If Phoenix is unavailable, the exporter logs
the failure without failing the bot's request; unexported traces are not durably queued.

The wiring is in [bootstrap/main.py](../src/safwa/bootstrap/main.py); agent and tool spans live in
[agent_runtime/tracing.py](../src/agent_runtime/tracing.py). The existing runtime `Observer` still
owns Safwa's stored steps and Telegram progress messages.

SDK documentation: [Phoenix OTEL setup](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/setup-using-phoenix-otel)
and [OpenAI instrumentation](https://arize.com/docs/phoenix/integrations/llm-providers/openai).
