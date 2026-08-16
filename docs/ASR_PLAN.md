# ASR plan — voice input for Safwa

Status: phase 1 implemented. Phase 2 (offline `faster-whisper`) is still a proposal.

## 1. Scope

The reviewed draft designs a portable C/C++ desktop transcription product: hardware router,
quantization profile matrix, VAD chunker, GPU worker queue, diarization, SRT/VTT/JSON writers,
several engine families. None of that is this project.

Safwa is a single-owner aiogram bot on Windows, `uv`-managed, Python 3.12. The audio it will ever
see is one Telegram voice note: OGG/Opus, mono, one speaker, never concurrent — `GenerationGuard`
already serializes the owner to one turn at a time. Under those constraints the pipeline is one
function: bytes in, text out.

Dropped from the draft, with reasons:

- Hardware detection and RTF benchmark — one owner, one machine, one line in `.env`. See §6.
- Profile matrix (`base-q5_1` / `small-q5_1` / `turbo-q5_0` / `turbo-q8_0`) — one `SAFWA_ASR_MODEL`.
- Explicit Silero VAD stage and silence-aware audio chunking — Whisper's long-form algorithm is
  inside every implementation already (30 s sliding windows advanced by decoded timestamps);
  `faster-whisper` bundles Silero (`vad_filter=True`) and hosted endpoints segment server-side.
  See §5 for what actually breaks at ten minutes — it is the text, not the audio.
- Temperature fallback, compression-ratio and logprob thresholds — already the default decode
  behavior of every Whisper implementation. Reimplementing it is rewriting the library.
- Diarization, SRT/VTT/JSON, timestamp merge — a bot posts a sentence, not a subtitle file.
- CPU/GPU worker queues — concurrency is one.
- Parakeet / GigaAM / Canary backends — an interface that admits them later costs nothing today;
  shipping them costs three model stacks.

Kept: Whisper as the model family, an engine interface above it, an explicit language setting.

## 2. The runtime decision

`whisper.cpp` is right for a distributable desktop binary and wrong as a Python dependency — its
Python bindings are third-party and lag the C library. The choice does not have to be made at all,
because a self-hosted whisper server (`whisper.cpp`'s own `server`, `faster-whisper-server`,
`speaches`) exposes **the same OpenAI-compatible `/v1/audio/transcriptions`** that OpenAI and Groq
expose.

One HTTP implementation therefore covers OpenAI, Groq and any local server, differing only by
`base_url` and `api_key` — exactly the `AIProvider` / `PROVIDER_DEFAULTS` shape already in
[config.py](../src/safwa/config.py), with **no new dependency**: `openai.AsyncOpenAI` is installed
and exposes `client.audio.transcriptions.create`.

A second, optional in-process engine (`faster-whisper`, CTranslate2) exists only to spare the owner
from running a server. Optional extra, not a core dependency.

## 3. Integration point — the part specific to this repo

Telegram is the canonical dialogue store. A voice message carries no text, so Telethon returns an
empty `raw_text` and [history.py](../src/safwa/history.py) skips it (`if not raw_text: continue`).
A voice note is therefore **invisible to the advisor** — the transcript has to exist as text in the
chat or it does not exist at all. The voice message itself stays in the chat as media and is never
read back, so nothing is duplicated.

The mechanism already exists. `materialize_queued_dialogue`
([_messaging.py](../src/safwa/telegram/_messaging.py)) posts a *bot* message marked
`DIALOGUE_USER`, and `recent` reads it back as `role="user"` (history.py:347). The transcript takes
the same path:

```
voice message
      │  (Telethon sees no text — invisible, correctly)
      ▼
guards: duration, file size
      ▼
download OGG bytes  (Bot API)
      ▼
Transcriber.transcribe(audio) -> TranscriptionResult
      ▼
split_telegram_text -> one or more
send_registered("<b>Owner:</b>\n<part>", kind=DIALOGUE_USER, replace=False)
      ▼
run_dialogue_turn(...)  — the existing ordinary_text loop, unchanged
```

Consequences of this shape:

- `history.py`, `ai/service.py`, `ai/contracts.py`, `domain.py` are **not touched**. The advisor
  never learns audio exists.
- Transcription runs *before* `guard.acquire`, so a voice note arriving mid-generation queues as
  plain text through the existing text-based queue, unchanged.
- A mis-transcription becomes permanent canonical dialogue, like a typo the owner sends. It is
  corrected by typing a correction. No confirm screen — that would be a second kind of dialogue
  turn to reason about for a case the owner can already fix in one message.

## 4. Long audio

A ten-minute Diary monologue is the real case, and audio length is not what breaks under it.

- **Download** — Opus at Telegram's voice bitrate is ~2.5 KB/s, so ten minutes is ~1.5 MB against
  the Bot API's 20 MB `getFile` cap. Upload to OpenAI/Groq is capped at 25 MB. Neither binds until
  roughly two hours.
- **Decoding** — the engine's job. Audio chunking would re-implement the library's long-form loop,
  worse: a fixed cut mid-word costs accuracy the built-in timestamp advance does not.
- **What actually breaks — the Telegram 4096-character limit.** Ten minutes of speech is roughly
  8–10 K characters. `split_telegram_text`
  ([_presentation.py](../src/safwa/telegram/_presentation.py):250) already exists for this and
  currently has no caller. Every part goes through `send_registered` with `kind=DIALOGUE_USER` and
  `replace=False`, so `recent` reads the monologue back as consecutive `role="user"` turns. The
  advisor runs **once**, after the last part. This is the one non-obvious piece of phase 1.
- **Token cost** — ~10 K characters is ~4 K tokens against `SUMMARY_TRIGGER_TOKENS = 8_000`. One
  monologue spends half the window and triggers a Summary sooner. That is the Summary working; no
  code, but worth knowing.
- **Timeout** — one HTTP call covers upload plus server-side decode of the whole file, so a flat
  60 s is wrong. Base plus a per-audio-second term, and `ASR_MAX_DURATION_SECONDS` (start at 1800)
  refuses anything longer with a plain message instead of hanging.

Audio chunking earns its place only above a provider file cap this bot will not reach. Defer it.
The genuine phase-2 want is *progress*: `faster-whisper` yields segments as it decodes, so the local
path can edit a "transcribing… 40 %" message instead of going silent for minutes.

## 5. Device — CPU vs GPU

Only the `faster_whisper` engine has a device. On every HTTP path the device is the server's problem
and the setting is ignored.

There it is one CTranslate2 parameter, not a benchmark: `WhisperModel(model, device="auto")` already
selects CUDA when the runtime finds it. What fails on Windows is not model selection but **missing
cuBLAS / cuDNN 9 DLLs on `PATH`** — CTranslate2 raises at model construction, so the handling is a
try/except at startup that falls back to `cpu` / `int8` and logs the reason once. That fallback is
the whole of "runtime profiling" worth having.

The draft's timed speech-sample benchmark exists to pick a model for an *unknown* machine. This bot
runs on one known machine whose owner feels the wait directly and edits one variable. A benchmark
spends startup time to guess what the owner already knows.

**Forcing CPU** is `SAFWA_ASR_DEVICE=cpu` — that is the whole feature, and it is deliberately the
same variable rather than a second overlapping `ASR_FORCE_CPU` boolean, so there is one source of
truth about which device is in use. Startup logs the resolved device and compute type once, so a
CPU-vs-GPU comparison is: set the variable, restart, send the same voice note, read the logged
elapsed time. `ASR_LOG_TIMING` (default on) puts audio duration, wall time and the resulting RTF in
the log line for exactly that measurement.

## 6. Language

One setting, two modes, nothing between them:

- `SAFWA_ASR_LANGUAGE=ru` — that language is passed as `language=` on every call. Detection never
  runs.
- empty — multilingual. The engine detects per request, as it does by default. Nothing is
  remembered between notes.

`.env.example` ships `SAFWA_ASR_LANGUAGE=ru` uncommented, because pinning is the better default for
a single owner.

There is no language cache. A cache would have to decide when a detection is trustworthy enough to
reuse, and getting that wrong pins the *wrong* language across many notes — strictly worse than
per-request detection, which fails one note at a time. Either the owner knows their language and
says so, or the engine decides fresh every time.

What the pin buys: Whisper detects language from the first 30 seconds only, so a short voice note
gives detection almost nothing. A wrong detection is not a small error — Whisper then decodes *into*
that language, producing a plausible sentence in the wrong one, or a translation. Nearby pairs
(ru/uk/bg, en/de, tr/az) are where it happens. Pinning removes that failure entirely and skips one
encoder pass. It does not lower baseline WER on clean audio; the gain is concentrated on short
notes, which is what this bot mostly receives.

Because the detected language is never read back, no response needs `verbose_json`, and no provider
or model is excluded on that basis.

## 7. Configuration surface

```
SAFWA_ASR_PROVIDER=off             # off | openai | groq | local | faster_whisper
SAFWA_ASR_MODEL=                   # unset -> per-provider default
SAFWA_ASR_API_KEY=
SAFWA_ASR_BASE_URL=                # unset -> ASR_PROVIDER_DEFAULTS
SAFWA_ASR_LANGUAGE=ru              # pinned language; empty = multilingual, detect per request
SAFWA_ASR_DEVICE=auto              # auto | cpu | cuda — faster_whisper only
SAFWA_ASR_COMPUTE_TYPE=            # unset -> int8 on CPU, float16 on CUDA
SAFWA_ASR_LOG_TIMING=true          # log audio seconds, wall seconds, RTF per transcription
```

`off` is the default, so existing installs keep behaving as they do and a voice note gets one plain
reply saying voice input is not configured.

`ASR_PROVIDER_DEFAULTS`, mirroring `PROVIDER_DEFAULTS` in [config.py](../src/safwa/config.py):

| provider | base_url | default model | notes |
|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini-transcribe` | |
| `groq` | `https://api.groq.com/openai/v1` | `whisper-large-v3-turbo` | recommended start |
| `local` | `http://127.0.0.1:8000/v1` | `Systran/faster-whisper-small` | any OpenAI-compatible server |
| `faster_whisper` | — (in-process) | `small` | needs the `asr-local` extra |

`groq` first: `whisper-large-v3-turbo`, 99 languages, negligible cost at voice-note volume, nothing
to install. `faster_whisper` on CPU `int8` is the fully-offline fallback; `small` is the sane default
there, `large-v3-turbo` the upgrade for a machine that can hold ~1.5 GB.

## 8. Module design — `src/safwa/asr.py`

A flat module beside `memory.py` and `history.py`. Imports nothing from `telegram/` or `ai/`.

```python
@dataclass(frozen=True)
class AudioClip:
    data: bytes
    filename: str          # "voice.ogg" — the provider infers the container from it
    mime_type: str         # "audio/ogg"
    duration_seconds: float

@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    elapsed_seconds: float = 0.0

class TranscriptionError(RuntimeError): ...

class Transcriber(Protocol):
    async def transcribe(self, clip: AudioClip) -> TranscriptionResult: ...
    async def close(self) -> None: ...
```

Concrete: `OpenAITranscriber`, `FasterWhisperTranscriber` (phase 2), `NullTranscriber`.
`build_transcriber(settings) -> Transcriber` is the only entry point `main.py` sees.

Language is a stored string or `None`, passed straight through to `language=`. No cache, no state.

Timeout is computed per clip:
`ASR_TIMEOUT_BASE_SECONDS + clip.duration_seconds * ASR_TIMEOUT_PER_AUDIO_SECOND`.

## 9. Files

New:

- `src/safwa/asr.py` — everything in §8.

Edited:

- [enums.py](../src/safwa/enums.py) — `ASRProvider` StrEnum
  (`OFF`, `OPENAI`, `GROQ`, `LOCAL`, `FASTER_WHISPER`).
- [constants.py](../src/safwa/constants.py) — `ASR_TIMEOUT_BASE_SECONDS`,
  `ASR_TIMEOUT_PER_AUDIO_SECOND`, `ASR_MAX_DURATION_SECONDS = 1800`,
  `ASR_MAX_FILE_BYTES` (Bot API caps downloads at 20 MB), the default base URLs and model names.
- [config.py](../src/safwa/config.py) — `asr_*` fields, `ASR_PROVIDER_DEFAULTS`,
  `resolved_asr_base_url` / `resolved_asr_model` / `resolved_asr_compute_type`, `asr_enabled`.
- [telegram/_core.py](../src/safwa/telegram/_core.py) — `Services.transcriber`.
- [telegram/_messaging.py](../src/safwa/telegram/_messaging.py) — `send_transcript(message,
  services, text) -> None`, which splits and registers every part. It belongs here because it sends
  and registers; the splitting itself stays in `_presentation.py`.
- [telegram/dialogue.py](../src/safwa/telegram/dialogue.py) — extract everything after
  `dismiss_prior_ui` in `ordinary_text` into
  `run_dialogue_turn(message, services, request, source)`; add
  `@router.message(F.voice | F.audio | F.video_note)`.
- [main.py](../src/safwa/main.py) — build the transcriber, inject it, close it in the polling
  `finally` beside the other services.
- `pyproject.toml` — `[project.optional-dependencies] asr-local = ["faster-whisper>=1.1,<2"]`.
- `docs/ARCHITECTURE.md`, `docs/STRUCTURE_GRAPH.md`, README env table, `.env.example`.

No new binary dependency: hosted endpoints accept `audio/ogg` directly and `faster-whisper` decodes
through bundled PyAV. `ffmpeg` is not required.

## 10. Phases

Two. Phase 1 is a working feature on its own; phase 2 only adds an engine behind the same interface.

### Phase 1 — voice input works, zero new dependencies

Covers OpenAI, Groq and any self-hosted OpenAI-compatible whisper server.

1. Extract `run_dialogue_turn` from `ordinary_text` — a pure refactor. Existing tests must pass
   unchanged. Separate commit, before anything else.
2. `ASRProvider` in `enums.py`, ASR constants in `constants.py`, `Settings` fields,
   `ASR_PROVIDER_DEFAULTS` and the resolved properties.
3. `asr.py`: `AudioClip`, `TranscriptionResult`, `TranscriptionError`, `Transcriber`,
   `NullTranscriber`, `OpenAITranscriber`, `build_transcriber`. Per-clip timeout, language passed
   through, timing logged.
4. `main.py` wiring, `Services.transcriber`, `send_transcript` in `_messaging.py`.
5. The voice handler on `F.voice | F.audio | F.video_note`: duration and size guards,
   `send_chat_action(TYPING)`, download, transcribe, `send_transcript`, `run_dialogue_turn`.
   `TranscriptionError` becomes a `MessageKind.ERROR` message.
6. e2e tests (§11), `docs/ARCHITECTURE.md`, `docs/STRUCTURE_GRAPH.md`, README, `.env.example`.

### Phase 2 — fully offline

`FasterWhisperTranscriber` behind the `asr-local` extra: lazy import, model built once at startup and
reused, `vad_filter=True`, `device` resolution with the logged CUDA→CPU fallback,
`asyncio.to_thread` so polling is never blocked. Segment-level progress on a throwaway message, since
a long local decode is otherwise silent for minutes. A missing extra fails at startup with a message
naming the install command.

Not planned: audio chunking (§4 — no provider cap this bot reaches), a `/settings` language control
(the env variable is the control), Whisper `prompt` biasing with Card titles (speculative, risks
hallucination).

## 11. Tests

Follow the `ScriptedProvider` pattern in `tests/e2e/conftest.py`: a `ScriptedTranscriber` replacing
only the network boundary, real SQLite, real services.

- A voice note produces one `DIALOGUE_USER` bot message whose text is the transcript, registered in
  `telegram_messages`.
- The advisor receives the transcript as a `user` turn — assert on the dialogue handed to
  `ScriptedProvider`.
- A transcript longer than `TELEGRAM_TEXT_LIMIT` becomes several `DIALOGUE_USER` messages, each
  registered, and the advisor runs **once**, after the last.
- A voice note arriving during generation is queued and processed in the next round.
- `TranscriptionError` posts an `ERROR` message and changes no planning data.
- `SAFWA_ASR_PROVIDER=off` answers with the not-configured message and calls no transcriber.
- Audio over `ASR_MAX_DURATION_SECONDS` is refused before download.
- A set `SAFWA_ASR_LANGUAGE` reaches the transcription call as `language=`; an empty one sends no
  `language` at all.

## 12. To verify before writing code

- Groq's current file-size cap for the account tier in use.
- `faster-whisper` wheel availability for Python 3.12 on Windows, and which CUDA/cuDNN major it
  expects, before promising the GPU path in the README.
