# Safwa

Safwa is a single-owner personal agile organizer and AI advisor delivered through a Telegram bot.
It supports hierarchical Goal/Idea/Action Cards, Checks, Values, Tags, Planning and Sprints (two
weeks by default), repeatable Actions, a Diary, Reminders, AI-authored saved Requests, voice input,
and an OpenAI-compatible persona. The advisor never writes to your data itself: every change is a
proposal, and it arrives as a Save/Discard screen unless it is one of the narrow allowlisted shapes
a second model review may approve on its own.

## Windows setup

```powershell
Copy-Item .env.example .env
uv sync --extra dev
uv run safwa-auth
uv run safwa
```

`SAFWA_AI_PROVIDER` selects the endpoint. The default `lmstudio` uses
`http://localhost:1234/v1`; `openrouter` uses `https://openrouter.ai/api/v1` and drops the
`temperature` parameter that GPT-5.6 and other reasoning models reject:

```dotenv
SAFWA_AI_PROVIDER=openrouter
SAFWA_AI_API_KEY=sk-or-v1-...
SAFWA_AI_MODEL=openai/gpt-5.6-luna
```

Every derived value (`SAFWA_AI_BASE_URL`, `SAFWA_AI_MAX_RETRIES`, `SAFWA_AI_SEND_TEMPERATURE`,
`SAFWA_AI_CACHE_BREAKPOINTS`, `SAFWA_AI_REASONING_EFFORT`) can still be set explicitly. Set the
model and Telegram credentials in `.env`. Set `SAFWA_TELEGRAM_BOT_USERNAME` without `@`; Safwa uses
it to build `t.me` links for Card, Check, Tag, Value, and Saved Request citations.

Safwa is still under active development and has no production database. Schema migrations are not
supported yet: after a schema change, rebuild the local SQLite database from scratch.

`SAFWA_TELEGRAM_API_ID` and `SAFWA_TELEGRAM_API_HASH` belong to the Telethon user-client, not the
bot. The Bot API cannot reread arbitrary chat history, while Safwa uses the Telegram conversation as
its canonical bounded advisor dialogue. Create the credentials at `my.telegram.org` and run
`uv run safwa-auth` once to authorize the local session file.

`data/memory.md` is the authoritative persistent persona memory. Keep exactly one non-empty fact
per line. Safwa imports local edits automatically and never treats its SQLite mirror as canonical.

## Voice input

`SAFWA_ASR_PROVIDER` is `off` by default, which keeps Safwa text-only. `groq`, `openai` and `local`
all speak the same OpenAI-compatible `/audio/transcriptions` API, so `local` covers any whisper
server you run yourself — `whisper.cpp`'s `server`, `faster-whisper-server`, `speaches` — through
`SAFWA_ASR_BASE_URL`:

```dotenv
SAFWA_ASR_PROVIDER=groq
SAFWA_ASR_API_KEY=gsk_...
SAFWA_ASR_LANGUAGE=ru
```

`faster_whisper` transcribes offline instead, in Safwa's own process — no server, no API key, no
network. It needs one extra, either the engine alone or the engine plus the CUDA runtime as wheels
(~1.3 GB), which Safwa registers itself:

```powershell
uv sync --extra asr-local
uv sync --extra asr-cuda
```

```dotenv
SAFWA_ASR_PROVIDER=faster_whisper
SAFWA_ASR_MODEL=large-v3-turbo
SAFWA_ASR_DEVICE=auto
SAFWA_ASR_LANGUAGE=ru
```

The model is fetched from Hugging Face on first start and cached — `small` is ~500 MB,
`large-v3-turbo` ~1.6 GB. `auto` uses the GPU when the CUDA runtime loads and otherwise transcribes
on the CPU, logging the reason once; `cpu` skips the attempt. `SAFWA_ASR_COMPUTE_TYPE` is `int8` on
the CPU and `float16` on CUDA unless you set it. A long recording shows a percentage while it
decodes.

Pin `SAFWA_ASR_LANGUAGE` to the language you speak. Left empty the engine detects one per message
from its first seconds, which misfires on short notes and then transcribes into the wrong language.

Safwa answers a voice message by posting its transcript as your own dialogue turn and replying to
that. A voice message carries no text, so the transcript is what the advisor reads; correct a bad
one by sending the correction as your next message. The turn is headed `User <your Telegram display
name>`, or just `User` when Telegram gives none.

## Architecture

`src/` holds four packages. `llm_gateway`, `agent_runtime` and `telegram_llm` import no Safwa at
all and are checked on it; `safwa` is the application built on them. The six flows are drawn in
[docs/diagrams/](docs/diagrams), a feature's wiring is [docs/FEATURE_MODULES.md](docs/FEATURE_MODULES.md),
and sessions and routing are [docs/AGENT_ARCH.md](docs/AGENT_ARCH.md).

```powershell
uv run python scripts/architecture_metrics.py
```

That prints the rules and the module graph. Every rule reads zero, and
`tests/test_architecture.py` fails on any violation.

### Definition of Done

The fourteen criteria this codebase is held to. `architecture_metrics.py` prints #1, #2, #3 and
#13 on every run; the rest are a rule, a snapshot or a review.

| # | Criterion | How it is measured | Result |
|---|---|---|---|
| 1 | Places to edit to add an entity | Rule H | one package plus one line in `MODULES` |
| 2 | No base Use Case | search for `UseCaseBase`, `class UseCase`, `def execute(self` | 0 |
| 3 | Largest module | `architecture_metrics.py` | 1 over 600: `features/cards/use_cases.py`, so every writer of an Action's stage is in one file |
| 4 | No module holds two of rules, data, use cases, manager, adapter | Rules A and K | 0 violations |
| 5 | Process state is a frozen union with one writer | Rule C | 0 violations |
| 6 | One Manager per process, each with a named identity | review | 3: `AgentManager` (a session), `TurnManager` (the turn), `CueRuntime` (what Safwa still owes) |
| 7 | A reducer only where a pure function simplifies the transitions | review | no quota, and none added without one |
| 8 | The shared packages work without Safwa | Rule F plus a running example | `examples/plain_chat_bot/` and `examples/note_keeper/`, both run by tests |
| 9 | Every rule cites a scenario | `tests/test_brd_traceability.py` | enforced |
| 10 | A test is replaced only on the owner's decision | review | the only one here nothing measures: the batch that drops a test names what still covers its scenario |
| 11 | The schema did not change outside a schema batch | Rule J | snapshot under `tests/snapshots/` |
| 12 | The prompt prefix is byte-stable | Rule I | snapshot under `tests/snapshots/` |
| 13 | No old path running beside a new one | search for facades | 0 |
| 14 | `ruff check .` and `pytest -q` | CI | green |

## Tests

```powershell
uv run pytest -q
uv run pytest tests\e2e -q
uv run ruff check .
```

The E2E suite uses a real migrated SQLite database and real Safwa application services while replacing
Telegram and the AI provider at their network boundaries. It never reads `.env` or contacts live services.

### Live Safwa-QA integration tests

Create a separate bot with BotFather, fill the `SAFWA_QA_*` variables from `.env.example`, and authorize
the separate QA user session once:

```powershell
uv run safwa-qa-auth
```

Stop any other process polling the Safwa-QA token, then run the explicitly gated live suite:

```powershell
uv run pytest tests\e2e\live --live-telegram -q
```

The live test sends `/status`, creates and reviews one Action through real Telegram messages and inline
callbacks, verifies the committed Card in a temporary SQLite database, deletes its QA chat messages, and
stops the test bot. It never uses the production bot token, database, `memory.md`, Telethon session, or AI
provider. Set `SAFWA_QA_KEEP_MESSAGES=true` when you want the QA conversation to remain visible after a
run; its inline buttons will be stale because the test database is temporary.

## Local backup and restore

Create a portable ZIP backup of the SQLite database and authoritative `data/memory.md`:

```powershell
uv run safwa-backup
```

The archive is written to `data/backups/` by default. To restore, stop Safwa first, then use the
explicit confirmation flag. Safwa validates the archive and creates a safety backup of the current data
before replacing it:

```powershell
uv run safwa-restore data\backups\safwa-YYYYMMDDTHHMMSSZ.zip --yes
```

Start Safwa again after the restore. A missing `memory.md` in a backup intentionally restores an empty
file-backed memory state.

## Bot navigation

Use `/start`, `/today`, `/sprint`, `/backlog`, `/values`, `/tags`, `/requests`, `/reminders`,
`/profile`, `/memory`, `/mem`, `/syncmem`, `/summarize`, `/status`, and `/cancel`.
Remove or edit durable facts directly in `data/memory.md`; the file watcher imports the change.

The advisor reads a window of the chat bounded by a token budget, so nothing has to be started or
ended. `/summarize` writes a `📜 Summary` on demand, which becomes the far edge of that window.

Creating a Card by hand stores nothing until **Save**: the draft lives in the screen and is gone if
you leave it.
