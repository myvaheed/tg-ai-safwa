# Safwa

Safwa is a single-owner personal agile organizer and AI advisor delivered through a Telegram bot.
It supports hierarchical Goal/Subgoal/Action Cards, Checks, Values, Tags,
Planning and Sprints (two
weeks by default), repeatable Actions, a Diary, Reminders, saved Requests Safwa writes, voice input,
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

Safwa's memory is written by the retro analysis of each Sprint alone and lives in the database;
`/memory` shows it, and what you want Safwa told outright goes in the Profile.

A screen that creates an item lists the open items most like it, compared by a local model.
The first start downloads that model, about 240 MB, into `data/models/`, and the list appears
once it has loaded. `SIMILAR_ITEMS` in `src/safwa/featuretoggles.py` turns it off, and then
nothing is downloaded.

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

`src/` holds five packages. `llm_gateway`, `agent_runtime`, `telegram_llm` and `tg_agent_shell`
import no Safwa at all and are checked on it; `safwa` is the application built on them. The
packages, the sessions and the flows they run are [docs/AGENT_ARCH.md](docs/AGENT_ARCH.md), and a
feature's wiring is [docs/FEATURE_MODULES.md](docs/FEATURE_MODULES.md).

```powershell
uv run python scripts/architecture_metrics.py
```

That prints the rules, the module graph and the counted metrics. Every rule reads zero and
`tests/test_architecture.py` fails on any violation; the metrics under them are read at review
and fail nothing.

### Definition of Done

What this codebase is held to. #1, #2, #3 and #13 are counted by `architecture_metrics.py` on
every run, so their current figures are read there rather than kept here; the rest are a rule, a
snapshot or a review.

| # | Criterion | How it is measured |
|---|---|---|
| 1 | Places to edit to add an entity — one package plus one line in `MODULES` | Rule H, counted by the scanner |
| 2 | No base Use Case | counted by the scanner |
| 3 | A module over 600 lines is reviewed for what it owns | counted by the scanner, which also lists the largest. A count, not a limit: the answer can be that the module is right as it is |
| 4 | No module holds two of rules, data, use cases, manager, adapter | Rules A and K |
| 5 | Process state is a frozen union with one writer | Rule C |
| 6 | One Manager per process, each with a named identity | review: `AgentManager` (a session), `TurnManager` (the turn), `CueRuntime` (what Safwa still owes) |
| 7 | A reducer only where a pure function simplifies the transitions | review — no quota, and none added without one |
| 8 | The shared packages work without Safwa | Rule F proves they import no Safwa, which is all an import graph can show. That a second application runs on them is shown by `examples/plain_chat_bot/` on the three libraries, `examples/note_keeper/` on the runtime, and `examples/wallet/` on the whole shell — a ledger of wallets and entries whose read, proposal, Save, restart and stale button `tests/shell/` runs with `safwa` unimportable |
| 9 | Every rule cites a scenario | `tests/test_brd_traceability.py` |
| 10 | A test is replaced only on the owner's decision | review — the one nothing measures: the batch that drops a test names what still covers its scenario |
| 11 | The schema did not change outside a schema batch | Rule J, snapshot under `tests/snapshots/` |
| 12 | The prompt prefix is byte-stable | Rule I, snapshot under `tests/snapshots/` |
| 13 | No old path running beside a new one | counted by the scanner |
| 14 | `ruff check .` and `pytest -q` | run by hand before a batch is finished. There is no CI in this repository, so nothing runs them for you |

## Tests

```powershell
uv run pytest -q
uv run pytest tests\e2e -q
uv run pytest tests\shell -q
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
stops the test bot. It never uses the production bot token, database, Telethon session, or AI
provider. Set `SAFWA_QA_KEEP_MESSAGES=true` when you want the QA conversation to remain visible after a
run; its inline buttons will be stale because the test database is temporary.

## Local backup and restore

Create a portable ZIP backup of the SQLite database:

```powershell
uv run safwa-backup
```

The archive is written to `data/backups/` by default. To restore, stop Safwa first, then use the
explicit confirmation flag. Safwa validates the archive and creates a safety backup of the current data
before replacing it:

```powershell
uv run safwa-restore data\backups\safwa-YYYYMMDDTHHMMSSZ.zip --yes
```

Start Safwa again after the restore.

## Bot navigation

Use `/start`, `/today`, `/sprint`, `/values`, `/tags`, `/requests`, `/reminders`, `/memory`,
`/summarize`, `/status`, and `/cancel`. The Backlog and the Profile are menu buttons only.

The advisor reads a window of the chat bounded by a token budget, so nothing has to be started or
ended. `/summarize` writes a `📜 Summary` on demand, which becomes the far edge of that window.

Creating a Card by hand stores nothing until **Save**: the draft lives in the screen and is gone if
you leave it.
