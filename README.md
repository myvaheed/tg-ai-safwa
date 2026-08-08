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

LM Studio defaults to `http://localhost:1234/v1`. Set the model and Telegram credentials in
`.env`. Safwa upgrades its SQLite schema at startup.

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
`/settings`, `/memory`, `/remember`, `/forget`, `/status`, and `/cancel`.

`/newsession <initial request>` begins an isolated persona branch. `/endsession [result instruction]`
asks for confirmation, compresses that branch into one visible `📦 Subsession request` context result,
then removes the branch's Telegram messages. The result becomes part of the parent Safwa dialogue; card,
Sprint, Value, and memory data are never deleted.

Every new card is first stored as an isolated draft. It reaches dashboards and metrics only after the
review screen's **Create** action.
