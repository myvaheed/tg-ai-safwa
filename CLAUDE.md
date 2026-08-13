# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/STRUCTURE_GRAPH.md](docs/STRUCTURE_GRAPH.md)
and [docs/MEMORY_HISTORY_USAGE.md](docs/MEMORY_HISTORY_USAGE.md) at the start of a session — they are
the fastest path to full context.

## Commands

Windows / PowerShell, `uv`-managed. Python is pinned to `>=3.12,<3.13`.

```powershell
uv sync --extra dev
uv run safwa                 # run the bot (long polling); creates missing SQLite tables first
uv run safwa-auth            # one-time Telethon user-session login (history reader)
uv run pytest -q
uv run pytest tests\e2e -q
uv run ruff check .
```

Single test / single file:

```powershell
uv run pytest tests\test_domain.py::test_parent_stage_propagation_and_reopen -q
```

`asyncio_mode = "auto"`, so async tests need no marker. Live Telegram tests are opt-in and skipped
unless `--live-telegram` is passed (`uv run pytest tests\e2e\live --live-telegram -q`); they need a
separate BotFather bot configured through the `SAFWA_QA_*` variables plus `uv run safwa-qa-auth`.

Backup/restore CLIs: `uv run safwa-backup`, `uv run safwa-restore <zip> --yes`.

`telegram-bot-exampler/` is an untracked local reference project, excluded from ruff — never edit it.

## Architecture

Single-owner Telegram bot (aiogram 3) + an OpenAI-compatible LLM (`SAFWA_AI_PROVIDER`, LM Studio
by default, OpenRouter for `openai/gpt-5.6-luna`) +
SQLite/SQLAlchemy 2 async. Flat modules under `src/safwa/`, wired in [main.py](src/safwa/main.py):
`Settings` → `Database` → provider/memory/advisor → `Services` dataclass injected as
`dispatcher["services"]`, plus three background `asyncio` tasks (memory file watcher, reminder
scheduler, daily memory maintenance) that are cancelled in the polling `finally`.

`docs/INITIAL_PLAN.md` and `docs/MEMORY_HISTORY_USAGE.md` are the authoritative product spec —
read them before changing history, memory, proposal, or UI behavior. `docs/ARCHITECTURE.md` and
`docs/STRUCTURE_GRAPH.md` map features and entities to files for fast orientation. The layer
responsibilities the spec names exist as flat files: [domain.py](src/safwa/domain.py)
(invariants + all mutations), [telegram/](src/safwa/telegram) (all UI, ~4.2k lines),
[ai/service.py](src/safwa/ai/service.py) (agent loop + proposals, ~1.9k lines).

Every limit, budget, cap, interval, and the effort scale live in
[constants.py](src/safwa/constants.py), which imports nothing from Safwa;
[config.py](src/safwa/config.py) takes its defaults from there. Put a new tuning number there, not
next to its use site.

The `telegram` package is layered, and imports run one way only:
[_core.py](src/safwa/telegram/_core.py) (`Services`, `router`, `GenerationGuard`, middleware, the
shared choice tables) ← [_presentation.py](src/safwa/telegram/_presentation.py) (pure text, labels,
markup, paging — touches neither a session nor the bot) ← [_messaging.py](src/safwa/telegram/_messaging.py)
(every send/edit/delete plus the `MessageKind` registration) ← the render modules
[cards.py](src/safwa/telegram/cards.py), [items.py](src/safwa/telegram/items.py),
[checks.py](src/safwa/telegram/checks.py) ← [screens.py](src/safwa/telegram/screens.py) and
[proposals.py](src/safwa/telegram/proposals.py) ← the handler modules
[commands.py](src/safwa/telegram/commands.py), [callbacks.py](src/safwa/telegram/callbacks.py),
[dialogue.py](src/safwa/telegram/dialogue.py). Only those last three register `@router` handlers, and
[__init__.py](src/safwa/telegram/__init__.py) imports them for that side effect — dropping one
silently unregisters its handlers. A leading underscore means module-local: a name used by a sibling
module carries no underscore, even though the whole package stays private behind `__init__.__all__`.

### Telegram is the canonical dialogue store, not SQLite

[history.py](src/safwa/history.py) re-reads the real private chat through Telethon on every advisor
turn. `telegram_messages` stores only `(chat_id, message_id, direction, kind)` — never persona text.
Consequences that break silently if ignored:

- Every bot message must be registered with a `MessageKind` (`send_registered`, `register_message`).
  An unregistered outgoing message is invisible to the LLM; a wrongly-kinded one leaks UI noise into
  persona history. Only `DIALOGUE_USER`, `DIALOGUE_ASSISTANT`, `REMINDER`, summaries, `/newsession`,
  and subsession results become dialogue.
- The kind is also carried *in the Telegram text*: `mark_kind` appends five invisible characters
  encoding it, `read_kind_mark` reads them back, so `telegram_messages` is a cache and a rebuilt
  database still recovers the dialogue. Mark every bot send — the sites outside `_messaging.py` are
  `main.memory_error`, `main.send_reminder`, `dialogue.send_summary` and the `/retro` caption — and
  never reuse a code in `_KIND_MARK_CODES`.
- Dialogue needs a visible boundary: a `/newsession <request>` message or the nearest `📜 Summary`.
  Without one, `recent(..., require_boundary=True)` raises `HistoryBoundaryMissing`.
- The middleware deletes every slash command except `/newsession` (which must stay visible as the
  boundary), and `delete_text_input` deletes typed field values as `UI_INPUT`.
- Bot API and Telethon use different message-ID spaces in a private chat; `_registered_message`
  correlates them exactly-once by ID then by ±15 s timestamp. Keep that pairing intact.

### AI mutations are always proposals

The model never mutates and never writes mutation SQL. Path:
tool call (`card`, `check`, `value`, `tag`, `request`, `remove`) →
Pydantic model in [ai/contracts.py](src/safwa/ai/contracts.py) → `AgentChange` →
`ChangeProposal` + `ProposalChange` rows → a read-only review screen with only **Save**/**Discard** →
`ProposalService.apply` calls the *same* `domain.py` functions the manual UI calls.
**Every** proposal screen is exactly Save/Discard; a screen that needs a field control is the wrong
screen. When the decision is the user's, the model **cites** the item instead of proposing one — no
tool, just Markdown in its reply ([telegram/screens.py](src/safwa/telegram/screens.py)):

- The model writes `[Milk](check:14)`; `render_citations` escapes the reply first, then rewrites each
  citation into `<a href="https://t.me/<bot>?start=check-14">`. The href is **built here from a
  validated id**, never taken from the model, and `?start=` accepts only `[A-Za-z0-9_-]`, which is
  why the payload separator is `-` while the model writes `:`.
- Citations are resolved before sending: an id that is missing or archived keeps its words and loses
  its link, because a `DIALOGUE_ASSISTANT` message stays in the chat for good and a dead link with it.
- The codec lives in [history.py](src/safwa/history.py) next to `mark_kind`, for the same reason:
  Telethon returns plain text, so `restore_citations` reads the link entities of every message back
  into `[Milk](check:14)`. Without it the model rereads its own citations as bare words and unlearns
  the format. Entity offsets are UTF-16 units — slice in surrogate space (`add_surrogate`).
- Tapping one sends `/start check-14`; the middleware deletes that command and `command_start`
  (`start_payload` → `open_citation`) sends the item's manual screen as a **new** message, so the
  reply above it survives as canonical dialogue. A vanished item answers `⚠️ Error while opening: …`
  and nothing else changes.
- A proposal outcome carries no reply text at all, so nothing competes with a review screen.

Multiple mutation calls in one turn become independent queued proposal screens in call order; the
queue lives in an `AgentStep` row with `kind="approval_batch"`. The model resumes only after the
last item resolves (`resolve_approval` → `continue_agent_approval`), receiving all mutation and read
results. Failed preparations return structured tool errors and are retried for at most
`MAX_REPAIR_ROUNDS = 5` (`MAX_TOOL_CALLS = 64`).

A suspended batch owns the whole request, not just its last tool call: it stores that request's
`dialogue` and its `transcript` (every assistant/tool message produced past the context prefix,
`AgentLoopResult.transcript`). `resolve_approval` rebuilds only the prefix and replays the transcript
with the decisions filled in, so the model keeps its own intermediate steps and does not re-read
Telegram to resume. Anything the model must know across an approval belongs in a tool result — the
resolved one carries `status`, `entity`, `action`, `summary`, `fields`, and `next`.

### Read-only SQL is triple-guarded

`query_safwa` and saved Requests accept one `SELECT`/`WITH … SELECT` over the `ai_*` views only.
Defenses in [ai/sql.py](src/safwa/ai/sql.py): regex validation (`validate_read_sql`), a separate
`mode=ro` sqlite3 connection with a `set_authorizer` allowlist, and row/column/payload/time caps.
The `ai_*` views are **dropped and rebuilt on every startup** (`create_ai_views`) — change view shape
there, not with a migration. New view ⇒ add it to `ALLOWED_VIEWS` *and* to the view list in
`SYSTEM_PROMPT` ([ai/context.py](src/safwa/ai/context.py)), or the model cannot use it. The same
function also drops the `card_search` FTS5 table and its triggers from older databases; Card
lookup is `query_safwa` over `ai_cards`.

### `data/memory.md` is authoritative

[memory.py](src/safwa/memory.py): UTF-8, one non-empty fact per line, ~4K-token budget. The
`memory_fact_cache` table is a disposable mirror — never treat it as the source. AI writes go through
`replace_facts`, which re-checks the file hash before *and* after writing a temp file, then
`os.replace`s, so a concurrent local edit is preserved rather than overwritten. An invalid or
oversized file disables memory injection instead of failing the turn.

### The prompt prefix must stay byte-stable

`_context_messages` ([ai/service.py](src/safwa/ai/service.py)) orders blocks by how often they
change so a remote provider can cache the prefix: `SYSTEM_PROMPT` alone, then planning state +
`memory.md`, then the dialogue, and only then a trailing `system` message carrying the clock.
`planning_context` returns `PlanningContext(state, clock)` for exactly that split. **New volatile
context goes after the dialogue, never into a system block** — one timestamp in `messages[0]` costs
every cache hit and also scatters OpenRouter's sticky provider routing, which keys on a hash of the
first system message. `SAFWA_AI_CACHE_BREAKPOINTS` adds `cache_control` markers at three positions
(both system blocks and the last dialogue message); OpenRouter converts them to OpenAI's
`prompt_cache_breakpoint` for GPT-5.6 and newer.

### Concurrency and UI state

- `GenerationGuard` is a single foreground lease keyed by the source `message_id`. While it is held,
  callbacks are rejected and new messages are deleted; `/cancel` and `/newsession` bypass it.
  Background reminders and memory maintenance check `guard.active` and stand down.
- `OwnerAndWritingMiddleware` drops anything that is not the owner in a private chat.
- Every inline button is a single-use `CallbackToken` row rendered as `cb:<token>` (24 h expiry);
  menu buttons use the `nav:<action>` prefix. `UiSession` holds transient editor state (manual card
  creation, text prompts) and is deleted on navigation — manual creation persists nothing until Save.
- Bot messages are sent with `parse_mode=HTML`; escape any user/model text with `html.escape`.
- `recover_startup` ([recovery.py](src/safwa/recovery.py)) reconciles interrupted runs, stale
  proposals, and expired tokens on every boot.

### Domain invariants worth knowing before editing

- Card tree: Goal is root-only; Idea may be root or under a Goal; Action may be root or under
  Goal/Idea and has no children. Action-only fields (effort, categories, energy, repeatable, liked)
  are stripped for Goal/Idea at both the AI and domain boundaries.
- Checks record a state observation, never planned work: no effort, never in a Sprint. A Check carries
  only a title and `repeatable`, and its outcome is `passed` or `missed`. **Pending is
  derived** (`outcome IS NULL`) and never stored. `resolved_at` holds the *first* resolution, because
  re-answering is allowed and must not move the observation the trend is keyed on. A repeatable Check
  spawns a successor **only on the Pending → resolved transition**; resolving through the Done-gate
  suppresses that spawn, which is the only thing stopping a repeatable Check from blocking its Card
  forever. `finish_action` gates `Done` (never `Cancelled`) and names the Pending ids and titles.
- A Card owns three links of one shape — Values, Tags, Checks — each with a `ReferenceSpec` in
  `CARD_REFERENCE_SPECS`, a `toggle_card_*` command and a `card_*` junction table. Adding a fourth means
  adding a spec, not a special case. `ReferenceSpec.name_attr` is why a Check (named by `title`)
  resolves through the same `*_query` path as a Tag or Value (named by `name`).
- A Card is a Check's only relationship, and it lives on the Card side in `card_checks`, so the `card`
  tool and the Card screen attach it — the `check` tool has no link mode and `create_check` takes no
  Cards. `toggle_card_check` is the only link-write path (`create_card(check_ids=…)` aside, where no
  Card exists yet to act on). One Check may hang on many Cards; one answer resolves it on all of them,
  and it gates every one until answered. Values and Tags do not classify Checks. A successor keeps
  only the linked Cards that are still live.
- Creating a Check, renaming it, and linking or unlinking it are **proposal-only** — the manual screens
  have no button for any of them. The Card screen shows `☑️ Checks` only when one already hangs there,
  and the Check screen offers exactly `🔁 Repeat` plus `✅ Passed` / `❌ Missed`, which write at once.
- A Check is answered with the Card lifecycle verbs: `check(mode="complete")` proposes Passed and
  `check(mode="cancel")` proposes Missed (`CHECK_ANSWER_ACTIONS` in [enums.py](src/safwa/enums.py)).
  The model proposes an answer only when the user already gave it; otherwise it opens the Check.
  Both manual answer screens — Check editor and Done-gate — use the same two buttons,
  `✅ Passed` / `❌ Missed`, never a cycling button. Nothing is prefilled, and the Done-gate's `Save`
  appears only once an answer is set.
- `manual_stage` is what the user set; `effective_stage` is derived for parents from descendants
  (`aggregate_child_stages`, `propagate_ancestors`) and is what dashboards and queries read.
- Effort is restricted to `EFFORT_POINTS` ([constants.py](src/safwa/constants.py)) `= {1,2,3,5,8,13}`
  and required for Actions. The `Literal` in `ai/contracts.py` mirrors it — change both together.
- Enums are `StrEnum` but columns store plain strings — always compare/assign `.value`.
- Entities carry a `version` for optimistic concurrency; `workspace.revision` is bumped on mutation
  and is what invalidates an in-flight AI answer. `StaleStateError` is the expected failure.
- Reminders are deterministic first: `ReminderPolicy.candidates` computes eligible candidates
  (quiet hours, cooldown, daily cap, dedupe key), `run_scheduler` takes the first, and only then does
  the LLM decide `{"send":bool,"message":str}` — it never chooses *what* to remind about.

## Schema gotcha

There are no migrations and no Alembic. `models.py` is the only schema source: startup calls
`upgrade_database` ([db.py](src/safwa/db.py)), which is `Base.metadata.create_all`.

`create_all` adds missing tables and indexes and **never alters an existing one**, so a **fresh**
database always matches `models.py`, while adding or changing a column will **not** touch an existing
`data/safwa.db`. A schema change therefore means editing `models.py` and rebuilding the database
(back it up first with `uv run safwa-backup`).

**Do not add Alembic or write migrations.** The product is pre-release and the owner recreates the
database instead. The owner will say when migrations start being written; the baseline is
autogenerated from `models.py` at that point.

## Conventions

- ruff `select = ["E","F","I","UP","B"]`, line length 100, `E501` ignored, target py312. All modules
  start with `from __future__ import annotations`.
- Comments are used sparingly and only to explain non-obvious *why* (Telegram/Telethon quirks,
  ordering constraints). Match that density; do not add narrative comments.
- Docs follow the same rule. Edit the fewest places that are actually wrong, and keep the edit as
  short as the line it replaces. Describe the behavior that exists now — never the design it
  replaced, why the old one was dropped, or how deliberate the new one is.
- User-facing strings are complete sentences and product-specific ("Card", "Sprint", "Value", "Tag",
  "Request" are capitalized domain nouns).
- Commit subjects in this repo follow `vX.Y <short summary>`.
- E2E tests use the real migrated SQLite database and real services, replacing only Telegram and the
  provider at their network boundaries (`ScriptedProvider` in `tests/e2e/conftest.py`). Keep new
  tests on that pattern rather than mocking domain functions.
- Never let QA/live test config touch production state: `resolve_qa_config`
  ([qa.py](src/safwa/qa.py)) hard-fails on a reused bot token or Telethon session path.
