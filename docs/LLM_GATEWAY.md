# `llm_gateway`

`llm_gateway` is a stateless boundary for one completion over the OpenAI chat-completions API.
It imports no application, database, validation or messaging library, and it is the only package
that knows an endpoint: its name, its address and the fields of its wire.

An application depends on `LlmProvider`: `complete(CompletionRequest)` returns a
`CompletionTurn`, and `aclose()` releases the client.  A request carries neutral messages, tool
specifications, a tool choice, a response schema, and temperature and reasoning hints.
`ToolCall.arguments_json` stays raw, so the tool's owner validates it and can answer with a
repair message.  `ScriptedProvider` returns scripted turns and records every request, for tests.

## One adapter, one table

`OpenAICompatibleProvider` is the only adapter.  What one endpoint needs and the next does not
is a field of `OpenAICompatibleConfig`, and each endpoint's values are one row of `PRESETS`
([presets.py](../src/llm_gateway/presets.py)).  `preset_config` builds a config from a row, with
every value given put over it.

| Row | Endpoint |
|---|---|
| `local` (default) | Any server on this machine that speaks the API: LM Studio at `http://localhost:1234/v1`; Ollama (`:11434/v1`), llama.cpp's `llama-server` (`:8080/v1`) or vLLM (`:8000/v1`) with their `base_url`. |
| `openrouter` | Every hosted model through one key.  It sends no temperature, which reasoning models refuse, and marks the prompt for Anthropic's cache. |

Another endpoint or gateway that speaks the API is one more row, and its line in `WIRE` in
[test_provider.py](../tests/test_provider.py).

## What goes back, and what is kept

- **Within a session, what the provider added goes back to it unchanged.**  Everything on the
  assistant message and on each call beyond the standard keys — a reasoning trace, a signature
  — is the turn's `extensions`, and `CompletionTurn.as_message` returns it on the next step.  No
  field is named for it, so a provider's new field works without a line here.
- **What outlives the session is the standard shape.**  `standard_message` keeps
  `STANDARD_KEYS` and nothing else, so a conversation one model wrote reads the same to the next.
- **A call id is one every endpoint takes back**: letters, digits, `_` and `-`, at most 40.  An
  id outside that, or one a response repeats, is replaced when the response is read.

## Placeholders for identifiers

`PseudonymizingProvider(inner, finder)` is an `LlmProvider` around another one. Before a request
leaves, what its `PiiFinder` finds in the request's texts goes as placeholders; when the answer
comes back, the placeholders are the words again. `HorizonPiiFinder` in
[horizon.py](../src/llm_gateway/horizon.py) is the finder Safwa composes; an application with
another model, or with rules of its own, brings its own `find`. What it hides for the owner, and
what it misses, is [SECURITY.md](SECURITY.md).

- **What is looked through:** a message's `content`, each text part of it, and every string in
  the arguments of its calls. A `system` message, an image, a tool's description and whatever a
  provider put in `extensions` go as they are.
- **A placeholder is the kind and a number,** `[PERSON_1]`, numbered by kind in the order its spelling first appears over
  the request's messages, one per spelling. A spelling with a letter in it is replaced wherever
  it recurs in the request as a whole word, also where the finder missed it: that is what hides
  a name in the answer restored on the step before. Digits alone are replaced only where found,
  since an age recurs as a date or a count.
- **Nothing is kept between requests** but the spans found in each text, for
  `FOUND_TEXTS_KEPT` texts. The placeholders are a function of the request, and a session's
  transcript only grows, so they keep their numbers from step to step and the cached prefix of
  the prompt stays cached.
- **Back:** a placeholder the request carried is replaced in `content`, and in `arguments_json`
  escaped for JSON, so a tool saves the real words. One it did not carry stays as written.
  `extensions` stay too: they go back to the same provider on the next step, where the numbers
  are the same.
- **It fails closed.** A finder that raises fails `complete` before the inner provider is
  called.

`HorizonPiiFinder.load` downloads the model once into the directory it is given and opens it
on the discrete graphics card `discrete_gpu` picks from `get_ep_devices` — the one the system
ranks first for high performance — through DirectML, or on the processor without one. On the
card a session has one fixed shape, a window of `WINDOW` tokens padded to its length: DirectML
then compiles the graph whole, instead of launching each of its operators on its own. A text
longer than a window is read in windows overlapping by `OVERLAP`, one at a time, under a lock,
since DirectML runs one call on a session at a time. `decode` grows each tagged span to its
whole words, makes a DATE within `BIRTH_CONTEXT_CHARS` after the application's birth words a
DATE_OF_BIRTH, and drops the kinds the application keeps.

## Models

As of September 2026.  What says run passed a live tool loop, the loop read back as kept, and
one photo; the rest is read from the providers' documentation.

| Row | Models | What breaks, and on which |
|---|---|---|
| `local` | Qwen3.6 35B-A3B, run on LM Studio 2026-09-27; Qwen3.5 4B and 9B; GLM-4.7-Flash; any model whose chat template renders tools | The server sets the context length, never the request, and Ollama's default is 4k under 24 GB of VRAM (`OLLAMA_CONTEXT_LENGTH`).  Ollama ignores `tool_choice`.  A template that does not render tool calls.  LM Studio turns reasoning off only with a `reasoning_effort` of "none", not with the chat template's own thinking switch (Qwen3.5 9B, 2026-10-18). |
| `openrouter` | `openai/gpt-5.6-luna`, in use; GPT-6; Claude Sonnet 5, Opus 5.5, Haiku 4.5; Gemini 3.x; Grok 4.x; DeepSeek V4; Qwen3.8; GLM-5.3 | Claude with thinking, a thinking Qwen and GLM-5.3 refuse `tool_choice="required"`: turn `tool_choice_required` off.  DeepSeek V4 thinking with tools wants the reasoning of every earlier turn back: send a `reasoning_effort` of "none". |

On either row, a model without vision refuses a request that carries an image.

Sources:
[Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) ·
[Ollama: context length](https://docs.ollama.com/context-length) ·
[OpenRouter: reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) ·
[DeepSeek: thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/) ·
[Z.ai: chat completion](https://docs.z.ai/api-reference/llm/chat-completion.md) ·
[Alibaba: function calling](https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling)
