# Safwa

Safwa is a single-owner personal agile organizer and AI advisor delivered through a Telegram bot.
It supports Tags, AI-authored saved Requests, hierarchical Goal/Idea/Action cards, Planning and two-week Sprints,
repeatable Actions, Values, energy/category analysis, mandatory card-draft review, reminders,
retrospective PNGs, and an OpenAI-compatible persona.

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
it to build `t.me` links for Card, Check, Tag, Value, and Saved Request citations. Safwa upgrades its
SQLite schema at startup.

`SAFWA_TELEGRAM_API_ID` and `SAFWA_TELEGRAM_API_HASH` belong to the Telethon user-client, not the
bot. The Bot API cannot reread arbitrary chat history, while Safwa uses the Telegram conversation as
its canonical bounded advisor dialogue. Create the credentials at `my.telegram.org` and run
`uv run safwa-auth` once to authorize the local session file.

`data/memory.md` is the authoritative persistent persona memory. Keep exactly one non-empty fact
per line. Safwa imports local edits automatically and never treats its SQLite mirror as canonical.

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

Use `/start`, `/today`, `/sprint`, `/backlog`, `/add`, `/drafts`, `/values`, `/advisor`, `/retro`,
`/settings`, `/memory`, `/mem`, `/syncmem`, `/status`, and `/cancel`. Remove or edit durable facts directly
in `data/memory.md`; the file watcher imports the change.

The advisor reads a window of the chat bounded by a token budget, so nothing has to be started or
ended. `/summarize` writes a `📜 Summary` on demand, which becomes the far edge of that window;
card, Sprint, Value, and memory data are never deleted.

Every new card is first stored as an isolated draft. It reaches dashboards and metrics only after the
review screen's **Create** action.
