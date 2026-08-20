# Safwa — Voice input (design record)

Why voice input is shaped the way it is. **Implemented**; the behaviour itself is described in
[ARCHITECTURE.md](ARCHITECTURE.md) (orientation) and [STRUCTURE_GRAPH.md](STRUCTURE_GRAPH.md)
(module index). This file keeps the reasoning and the rejected alternatives, which those documents
do not carry.

## Constraints

Safwa is a single-owner aiogram bot on Windows, `uv`-managed, Python 3.12. The audio it will ever
see is one Telegram voice note: OGG/Opus, mono, one speaker, never concurrent — `GenerationGuard`
already serializes the owner to one turn at a time. Under those constraints the pipeline is one
function: bytes in, text out.

## Two engines, one interface

A self-hosted whisper server (`whisper.cpp`'s own `server`, `faster-whisper-server`, `speaches`)
exposes **the same OpenAI-compatible `/v1/audio/transcriptions`** that OpenAI and Groq expose. One
HTTP implementation therefore covers all three, differing only by `base_url` and `api_key` — the
`AIProvider` / `PROVIDER_DEFAULTS` shape already in [config.py](../src/safwa/config.py), and with no
new dependency, since `openai.AsyncOpenAI` is installed and exposes
`client.audio.transcriptions.create`.

`faster_whisper` (CTranslate2, in this process) is the second engine, and exists only to spare the
owner from running a server at all. It is an optional extra, never a core dependency: `asr-local`
for the engine, `asr-cuda` for the engine plus the CUDA runtime wheels.

`whisper.cpp` is right for a distributable desktop binary and wrong as a Python dependency — its
Python bindings are third-party and lag the C library. As a server it is reachable through `local`
anyway, so the choice never has to be made.

No new binary dependency either: hosted endpoints accept `audio/ogg` directly and `faster-whisper`
decodes through bundled PyAV. `ffmpeg` is not required.

## Integration — the part specific to this repo

Telegram is the canonical dialogue store. A voice message carries no text, so Telethon returns an
empty `raw_text` and [history.py](../src/safwa/history.py) skips it. A voice note is therefore
**invisible to the advisor** — the transcript has to exist as text in the chat or it does not exist
at all. The voice message itself stays in the chat as media and is never read back, so nothing is
duplicated.

The mechanism already existed for the queue drain: `send_owner_turn`
([_messaging.py](../src/safwa/telegram/_messaging.py)) posts a *bot* message marked `DIALOGUE_USER`,
and `recent` reads it back as `role="user"`. The transcript takes the same path:

```
voice message
      │  (Telethon sees no text — invisible, correctly)
      ▼
guards: duration, file size
      ▼
download OGG bytes  (Bot API)
      ▼
Transcriber.transcribe(clip, progress=…) -> TranscriptionResult
      ▼
send_owner_turn -> split_telegram_text -> one or more DIALOGUE_USER bot messages
      ▼
run_dialogue_turn(...)  — the same loop ordinary text uses
```

Consequences of this shape:

- `history.py`, `ai/service.py`, `ai/contracts.py`, `domain.py` are **not touched**. The advisor
  never learns audio exists.
- Transcription runs *before* `guard.acquire`, so a voice note arriving mid-generation queues as
  plain text through the existing text-based queue, unchanged. The lease is read after the decode,
  never before it: a decode lasts long enough for the lease to have been taken, dropped or handed to
  a background generation in the meantime.
- A mis-transcription becomes permanent canonical dialogue, like a typo the owner sends. It is
  corrected by typing a correction. No confirm screen — that would be a second kind of dialogue turn
  to reason about for a case the owner can already fix in one message.

## Long audio

A ten-minute Diary monologue is the real case, and audio length is not what breaks under it.

- **Download** — Opus at Telegram's voice bitrate is ~2.5 KB/s, so ten minutes is ~1.5 MB against
  the Bot API's 20 MB `getFile` cap. Upload to OpenAI/Groq is capped at 25 MB. Neither binds until
  roughly two hours.
- **Decoding** — the engine's job. Whisper's long-form algorithm is inside every implementation
  already: 30 s sliding windows advanced by decoded timestamps. Audio chunking would re-implement
  that loop, worse, because a fixed cut mid-word costs accuracy the timestamp advance does not.
- **What actually breaks — the Telegram 4096-character limit.** Ten minutes of speech is roughly
  8–10 K characters, so `split_telegram_text` posts several `DIALOGUE_USER` messages and `recent`
  reads the monologue back as consecutive `role="user"` turns. The advisor runs **once**, after the
  last part. This is the one non-obvious piece.
- **Token cost** — ~10 K characters is ~4 K tokens against `SUMMARY_TRIGGER_TOKENS = 8_000`. One
  monologue spends half the window and triggers a Summary sooner. That is the Summary working.
- **Timeout** — an HTTP call covers upload plus server-side decode of the whole file, so a flat 60 s
  is wrong: the budget is a base plus a per-audio-second term. `ASR_MAX_DURATION_SECONDS` refuses
  anything longer than half an hour with a plain message instead of hanging.

The local engine carries no timeout at all. Cancelling the wait cannot stop the thread, so
`wait_for` would abandon the work rather than end it, and a local decode has no network to hang on.
The duration guard before the download is what bounds it.

**Progress** is the local engine's answer to a decode that is otherwise silent for minutes: it
yields segments, so `transcribe(progress=…)` reports decoded seconds and the handler edits one
message with a percentage. That message is a `MessageKind.STATUS` — a kind of its own, because every
existing one either means something else in the chat or is swept by `dismiss_prior_ui` — and
`_TranscriptionProgress` deletes it in the handler's `finally`. Reporting is throttled and starts
only past `ASR_PROGRESS_MIN_AUDIO_SECONDS`, since a shorter clip finishes before the first edit
would land. The callback is what keeps `asr.py` free of any `telegram/` import.

## Device — CPU vs GPU

Only the `faster_whisper` engine has a device. On every HTTP path the device is the server's problem
and the setting is ignored.

There it is one CTranslate2 parameter, not a benchmark. What fails on Windows is not model selection
but **missing cuBLAS / cuDNN 9 DLLs**, and CTranslate2 loads those with its first kernel rather than
when the model is built — so the raise lands at construction on one machine and a whole decode later
on another. Both are caught: startup falls back to `cpu` / `int8`, and a decode failure naming a CUDA
library rebuilds on the CPU and retries that recording. Logged once each. That fallback is the whole
of "runtime profiling" worth having; a timed speech-sample benchmark exists to pick a model for an
*unknown* machine, and this bot runs on one machine whose owner feels the wait directly.

The `asr-cuda` extra removes the setup step: `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` are wheels,
so the DLLs arrive with `uv sync`. They land in `site-packages/nvidia/*/bin`, where nothing looks by
itself, so `_register_cuda_runtime` registers those directories before `faster_whisper` is imported —
through `add_dll_directory` **and** `PATH`, because the lazy cuBLAS load goes through the ordinary
search order, which the first does not reach. Without both the extra installs and is never found.

**Forcing CPU** is `SAFWA_ASR_DEVICE=cpu`, deliberately the same variable rather than a second
overlapping `ASR_FORCE_CPU` boolean, so there is one source of truth about which device is in use.
Startup logs the resolved device and compute type once, and `SAFWA_ASR_LOG_TIMING` (default on) puts
audio duration, wall time and the resulting RTF in a line per transcription — so a CPU-vs-GPU
comparison is: set the variable, restart, send the same voice note, read the log.

## Language

One setting, two modes, nothing between them:

- `SAFWA_ASR_LANGUAGE=ru` — that language is passed as `language=` on every call. Detection never
  runs.
- empty — multilingual. The engine detects per request, as it does by default. Nothing is remembered
  between notes.

`.env.example` ships `SAFWA_ASR_LANGUAGE=ru` uncommented, because pinning is the better default for
a single owner.

There is no language cache. A cache would have to decide when a detection is trustworthy enough to
reuse, and getting that wrong pins the *wrong* language across many notes — strictly worse than
per-request detection, which fails one note at a time.

What the pin buys: Whisper detects language from the first 30 seconds only, so a short voice note
gives detection almost nothing. A wrong detection is not a small error — Whisper then decodes *into*
that language, producing a plausible sentence in the wrong one, or a translation. Nearby pairs
(ru/uk/bg, en/de, tr/az) are where it happens. Pinning removes that failure entirely and skips one
encoder pass. It does not lower baseline WER on clean audio; the gain is concentrated on short notes,
which is what this bot mostly receives.

Because the detected language is never read back, no response needs `verbose_json`, and no provider
or model is excluded on that basis.

## Configuration surface

```
SAFWA_ASR_PROVIDER=off             # off | openai | groq | local | faster_whisper
SAFWA_ASR_MODEL=                   # unset -> per-provider default
SAFWA_ASR_API_KEY=                 # openai | groq
SAFWA_ASR_BASE_URL=                # unset -> ASR_DEFAULTS
SAFWA_ASR_LANGUAGE=ru              # pinned language; empty = multilingual, detect per request
SAFWA_ASR_DEVICE=auto              # auto | cpu | cuda — faster_whisper only
SAFWA_ASR_COMPUTE_TYPE=            # unset -> int8 on CPU, float16 on CUDA
SAFWA_ASR_LOG_TIMING=true          # log audio seconds, wall seconds, RTF per transcription
```

`off` is the default, so an install that does not configure voice keeps behaving as it does and a
voice note gets one plain reply saying voice input is off.

`ASR_DEFAULTS`, mirroring `PROVIDER_DEFAULTS` in [config.py](../src/safwa/config.py):

| provider | base_url | default model | notes |
|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini-transcribe` | |
| `groq` | `https://api.groq.com/openai/v1` | `whisper-large-v3-turbo` | recommended start |
| `local` | `http://127.0.0.1:8000/v1` | `Systran/faster-whisper-small` | any OpenAI-compatible server |
| `faster_whisper` | — (in-process) | `small` | needs `asr-local`, or `asr-cuda` for the GPU |

`groq` first: 99 languages, negligible cost at voice-note volume, nothing to install.
`faster_whisper` on CPU `int8` is the fully-offline fallback; `small` is the sane default there,
`large-v3-turbo` the upgrade for a machine that can hold ~1.5 GB. A `faster_whisper` model name is
resolved by the library — a bare size downloads a converted CTranslate2 repository from Hugging
Face, and a path is used as it stands.

## Module — `src/safwa/asr.py`

A flat module beside `memory.py` and `history.py`. Imports nothing from `telegram/` or `ai/`.

```python
@dataclass(frozen=True)
class AudioClip:
    data: bytes
    filename: str          # "voice.ogg" — the endpoint reads the container from it
    mime_type: str         # "audio/ogg"
    duration_seconds: float

@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    elapsed_seconds: float = 0.0

class TranscriptionError(RuntimeError): ...

ProgressCallback = Callable[[float, float], Awaitable[None]]   # decoded seconds, total

class Transcriber(Protocol):
    async def transcribe(
        self, clip: AudioClip, *, progress: ProgressCallback | None = None
    ) -> TranscriptionResult: ...
    async def close(self) -> None: ...
```

Concrete: `OpenAITranscriber`, which ignores `progress` because one response carries the whole
transcript, and `FasterWhisperTranscriber`, which builds its model once at startup and decodes in
`asyncio.to_thread` so polling is never blocked. `build_transcriber(settings) -> Transcriber | None`
is the only entry point `main.py` sees, and `None` is what `off` means. A missing `asr-local` extra
fails at startup with the install command.

Language is a stored string or `None`, passed straight through to `language=`. No cache, no state.

## Tests

Follow the `ScriptedProvider` pattern in `tests/e2e/conftest.py`: a `ScriptedTranscriber` replacing
only the network boundary, real SQLite, real services. The offline engine is covered over a fake
`WhisperModel`, so no test downloads weights.

- A voice note produces one `DIALOGUE_USER` bot message whose text is the transcript, registered in
  `telegram_messages`, and the advisor receives it as a `user` turn.
- A transcript longer than `TELEGRAM_TEXT_LIMIT` becomes several `DIALOGUE_USER` messages, each
  registered, and the advisor runs **once**, after the last.
- A voice note arriving during generation is queued and processed in the next round.
- `TranscriptionError` posts an `ERROR` message and changes no planning data.
- `SAFWA_ASR_PROVIDER=off` answers with the not-configured message and calls no transcriber.
- Audio over `ASR_MAX_DURATION_SECONDS` is refused before download.
- A set `SAFWA_ASR_LANGUAGE` reaches the transcription call as `language=`; an empty one sends no
  `language` at all.
- The offline engine: a failed CUDA load falls back to `cpu`/`int8` whether it fails at build time or
  mid-decode, `cpu` never attempts CUDA, segments join into one transcript, a decode failure becomes
  a `TranscriptionError`, and progress is throttled and skipped entirely on a short clip.
- A progress report posts one `STATUS` message, edits it, and deletes it with its row.

## Rejected

- **Hardware detection and an RTF benchmark** — one owner, one machine, one line in `.env`.
- **A quantization profile matrix** — one `SAFWA_ASR_MODEL`.
- **An explicit VAD stage and silence-aware chunking** — `faster-whisper` bundles Silero
  (`vad_filter=True`) and hosted endpoints segment server-side. Chunking earns its place only above
  a provider file cap this bot does not reach.
- **Temperature fallback, compression-ratio and logprob thresholds** — already the default decode
  behaviour of every Whisper implementation. Reimplementing it is rewriting the library.
- **Diarization, SRT/VTT/JSON, timestamp merge** — a bot posts a sentence, not a subtitle file.
- **CPU/GPU worker queues** — concurrency is one.
- **Parakeet / GigaAM / Canary backends** — the interface admits them later at no cost today;
  shipping them costs three model stacks.
- **A `/settings` language control** — the environment variable is the control.
- **Whisper `prompt` biasing with Card titles** — speculative, and it risks hallucination.
