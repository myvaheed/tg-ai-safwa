# Safwa — Architecture

Fast orientation map for a new session. Companion: [STRUCTURE_GRAPH.md](STRUCTURE_GRAPH.md)
(module/entity index), [INITIAL_PLAN.md](INITIAL_PLAN.md) + [MEMORY_HISTORY_USAGE.md](MEMORY_HISTORY_USAGE.md)
(product spec), [INCONSISTENCIES.md](INCONSISTENCIES.md) (spec vs code drift).

## What it is

Single-owner personal agile advisor. One private Telegram chat, aiogram 3 long polling, a local
OpenAI-compatible LLM (LM Studio default), SQLite/SQLAlchemy 2 async. Runs on Windows, `uv`-managed,
Python `>=3.12,<3.13`. No server, no multi-user, no Mini App.

## Runtime wiring

`main.main()` → `upgrade_database` (`create_all`) → `asyncio.run(main.run(settings))`
([main.py:56](../src/safwa/main.py:56)):

1. `Settings` (pydantic-settings, `SAFWA_` prefix, `.env`) — [config.py](../src/safwa/config.py)
2. `Database` — async engine + `PRAGMA foreign_keys/WAL/busy_timeout` ([db.py:37](../src/safwa/db.py:37))
3. one boot session: `bootstrap_workspace` → `recover_startup` → `create_ai_views`
4. `OpenAICompatibleProvider` → `MemoryFileStore.sync()` → `ReadOnlyQueryRunner` → `AIAdvisor`
5. `Bot` (`parse_mode=HTML`) → `TelegramHistorySource.from_settings(..., bot_user_id=me.id)` → `.start()`
6. `PersonaContinuity` → `GenerationGuard` → `Services` dataclass → `dispatcher["services"]`
7. `OwnerAndWritingMiddleware` on both message and callback outer middleware; `router` included
8. `set_my_commands` (21 commands)
9. two background tasks (plus the opt-in reminder scheduler), all cancelled in the polling `finally`:
   - `memory.poll(memory_error)` — 5 s `memory.md` hash watcher
   - `run_scheduler(..., ReminderPolicy, send_reminder)` — 30 s reminder loop, only when
     `SAFWA_SCHEDULER_ENABLED=true` (disabled by default while logging is verified)
   - `run_memory_maintenance(...)` — 60 s daily-memory-sync eligibility loop

`send_reminder` and memory maintenance both stand down while `guard.active`.

## Layering

Flat modules under `src/safwa/` plus two packages (`ai/`, `telegram/`) — one file per responsibility,
not one package per layer:

| Responsibility | Files |
|---|---|
| domain | [domain.py](../src/safwa/domain.py), [enums.py](../src/safwa/enums.py), [models.py](../src/safwa/models.py), [saved_requests.py](../src/safwa/saved_requests.py) |
| application | [domain.py](../src/safwa/domain.py) (mutations), [ai/service.py](../src/safwa/ai/service.py) (`ProposalService`), [continuity.py](../src/safwa/continuity.py), [scheduler.py](../src/safwa/scheduler.py), [analytics.py](../src/safwa/analytics.py) |
| infrastructure | [db.py](../src/safwa/db.py), [history.py](../src/safwa/history.py), [memory.py](../src/safwa/memory.py), [ai/provider.py](../src/safwa/ai/provider.py), [ai/sql.py](../src/safwa/ai/sql.py), [backup.py](../src/safwa/backup.py) |
| telegram | [telegram/](../src/safwa/telegram) (10 modules, ~4.6k lines) |
| ai | [ai/](../src/safwa/ai) (context, contracts, provider, service, sql) |
| bootstrap | [main.py](../src/safwa/main.py), [config.py](../src/safwa/config.py), [constants.py](../src/safwa/constants.py), [recovery.py](../src/safwa/recovery.py), [qa.py](../src/safwa/qa.py) |

[constants.py](../src/safwa/constants.py) holds every limit, budget, cap, interval, and the effort
scale. It imports nothing from Safwa, so any module may import it and `config.py` takes its defaults
from there without an import cycle. A new tuning number goes there, not next to its use site.

### `telegram` package — one-way import chain

```
_core.py            Services, router, GenerationGuard, middleware, CallbackContext, choice tables
   ↑
_presentation.py    pure text/labels/markup/paging — no session, no bot
   ↑
_messaging.py       every send/edit/delete + MessageKind registration + token buttons
   ↑
cards.py  checks.py  items.py  proposals.py    render modules
   ↑
commands.py  callbacks.py  dialogue.py  the ONLY @router handlers
```

`__init__.py` imports the three handler modules for their registration side effect — dropping one
silently unregisters its handlers. Leading underscore = module-local; a name used by a sibling has
no underscore (the whole package is private behind `__init__.__all__`).

## Feature map

### Planning domain

- **Cards**: strict tree. Goal root-only; Idea root or under Goal; Action root or under Goal/Idea,
  no children. Stages `backlog|sprint|today|done|cancelled`.
- `manual_stage` = what the user set. `effective_stage` = derived for parents from descendants
  (`aggregate_child_stages` → live max by `LIVE_STAGE_PRECEDENCE`, else all-cancelled → Cancelled,
  else Done) and propagated up by `propagate_ancestors`. Dashboards/queries read `effective_stage`.
- Action-only fields: `effort_points` (`constants.EFFORT_POINTS = {1,2,3,5,8,13}`, required), `repeatable`,
  categories, energy types, `liked`. Stripped for Goal/Idea at both the AI and domain boundaries.
- `blocked` is warning-only, requires non-empty `blocked_description`. No Card-to-Card dependency graph.
- Priority `critical|medium|low`; `hard_time` independent boolean.
- **Values / Tags**: many-to-many Card *and* Check classification. Values have `active` (AI focus); Tags do not.
- **Checks**: a state observation ("did this hold?"), not planned work — no effort, never in a Sprint.
  Owned by a Card (`card_id`) or standalone. `outcome` is `passed|failed|not_applicable`; **Pending is
  derived** (`outcome IS NULL`) and never stored, so there is no reset path. `resolved_at` keeps the
  *first* resolution — re-answering overwrites the outcome and the previous one is not retained.
  `series_id`/`source_instance_id` mirror the Card repeat lineage. A repeatable Check spawns a Pending
  successor **only on the Pending → resolved transition**, and never onto a terminal or archived Card.
- **Done-gate**: `finish_action` refuses `Done` while a Card has Pending Checks and names their ids and
  titles; `Cancelled` is not gated. Resolving through the gate suppresses the spawn, which is what stops
  a repeatable Check from blocking its own Card forever. `_copy_repeat_successor` clones one Pending
  copy per Check series (grouping matters — an in-cycle spawn leaves two rows of one series on the Card).
- **Repeatable Actions**: `finish_action` → `_copy_repeat_successor` clones parent, text, priority,
  hard_time, blocked, effort, and all four link sets into a successor at the prior live stage.
- **Sprints**: `start_sprint` (Planning only) snapshots every non-archived Action in Sprint/Today as
  `scope_kind="initial"` commitments; planned length 14 calendar days (`start + 13 days`);
  `finish_sprint` = Finish Early, no pause. `_sync_commitment_for_stage` records later add/remove.
- **Archive/delete**: `archive_subtree` (reversible, keeps events), `delete_subtree` (needs a second
  destructive confirmation), `archive_tag`/`archive_value` drop links atomically and clear focus.
- **Optimistic concurrency**: every entity has `version`; `workspace.revision` bumps on mutation and
  invalidates an in-flight AI answer. Expected failure: `StaleStateError`.
- **Audit**: `card_events` (actor, operation, before/after snapshot, correlation, sprint).

### Telegram UI

- Dashboards `/today` `/backlog` `/sprint` list **Actions only**; Goals/Ideas reachable via hierarchy,
  `Children`, search, Requests, item navigation.
- Checks are item-shaped, not Card-shaped ([telegram/checks.py](../src/safwa/telegram/checks.py)). A
  `Checks` button appears on every Card screen and on a Tag/Value screen that has linked Checks.
  Pressing `Done` on a gated Card opens the resolution screen instead of finishing it: every Pending
  Check defaults to **Missed**, tapping cycles Passed → Missed → Not applicable, `Save` finishes the
  Card in one transaction and `Back` leaves it live. The default is deliberately not Passed — a one-tap
  "all done" would let the gate be cleared by asserting Checks that never happened.
  A Check with no Card, Value, or Tag is reachable only through `ai_checks`; there is no `/checks` yet.
- Every inline button is a single-use `CallbackToken` row rendered as `cb:<token>` (24 h expiry),
  claimed atomically by `UPDATE … RETURNING` in `callback_token_handler`. Menu buttons use `nav:<action>`.
- `CALLBACK_ACTIONS` ([callbacks.py:814](../src/safwa/telegram/callbacks.py:814)) is the action→handler registry.
- `UiSession` holds transient editor state (`card_create`, `card_create_text`, `card_text`,
  `card_blocked_text`, `item_text`); deleted on navigation. Manual Card creation persists **nothing**
  until Save.
- Text-field editing replaces the screen with a focused prompt; the typed reply is deleted as
  `UI_INPUT` and the item screen is restored (`delete_text_input` + `edit_registered_message`).
- `dismiss_prior_ui` removes stale `DASHBOARD`/`CARD_EDITOR`/`APPROVAL` screens before new dialogue
  and converts an unanswered proposal into a static result message.
- All bot text is HTML — escape user/model text with `html.escape`.

### AI advisor

Path: ordinary text → `dialogue.ordinary_text` → `guard.acquire` → `history.dialogue()` →
`AIAdvisor.handle` → agent loop → proposals or a final message.

- Tools: `query_safwa(sql)` (immediate read) + mutation tools `card`, `check`, `value`, `tag`, `request`,
  `remove` (`SAFWA_TOOLS`, [ai/service.py:133](../src/safwa/ai/service.py:133)).
- `_guard_pending_checks` refuses to *prepare* a completion while Pending Checks exist, returning a
  retryable `ToolPreparationError` that carries their ids **and titles** so the model does not spend a
  `query_safwa` round finding them. It then calls `check(mode="resolve_for_card")`, whose proposal screen
  is the one place field controls appear — the model proposes which Checks to answer, the user answers.
- **The model never mutates and never writes mutation SQL.** Tool call → Pydantic model in
  [ai/contracts.py](../src/safwa/ai/contracts.py) → `AgentChange` → `ChangeProposal` + `ProposalChange`
  rows → a read-only review screen with only **Save**/**Discard** → `ProposalService.apply` calls the
  *same* `domain.py` functions the manual UI calls.
- Multiple mutation calls in one turn become independent queued proposal screens in call order; the
  queue lives in an `AgentStep` row with `kind="approval_batch"`. The model resumes only after the last
  item resolves (`resolve_approval` → `continue_agent_approval`) and receives all mutation and read results.
- Failed preparations return structured `ToolPreparationError` results and are retried for at most
  `MAX_REPAIR_ROUNDS = 5`; `MAX_TOOL_CALLS = 64`.
- Earlier batch summaries are injected into the next tool-call assistant message
  (`_assistant_content_with_request_progress`) and never enter canonical history, summaries, or memory.
- Context = one system message (`SYSTEM_PROMPT` + `planning_context()` + `memory.text`) followed by the
  canonical dialogue turns the history source already bounded. `planning_context` carries local time,
  workspace mode, About Me, advisor instructions, active Values, available Tags, and Today Actions —
  deliberately **no** Sprint metrics and **no** precomputed Card candidates; the model reaches those
  through `query_safwa`.

### Read-only SQL — triple guard

[ai/sql.py](../src/safwa/ai/sql.py): one `SELECT`/`WITH … SELECT` over `ai_*` views only.

1. `validate_read_sql` — single statement, `FORBIDDEN` keyword regex, FROM/JOIN name allowlist
   (`ALLOWED_VIEWS`) with CTE names subtracted.
2. `ReadOnlyQueryRunner` — separate `sqlite3` `mode=ro` connection with a `set_authorizer` allowlist.
3. Caps (all in [constants.py](../src/safwa/constants.py)) — `DEFAULT_ROW_LIMIT=50`,
   `DEFAULT_CHAR_BUDGET=12_000`, `DEFAULT_COLUMN_LIMIT=20`, `DEFAULT_CELL_LIMIT=2_000`,
   `QUERY_TIMEOUT_SECONDS=2.0`; a capped result returns a `notice`.

`ai_checks` exposes `status` as `COALESCE(outcome, 'pending')`, so a query never has to know that
Pending is a null column. It is also the only route to a Check with no Card, Value, or Tag.

Views are dropped and rebuilt by `create_ai_views` **on every startup** — change view shape there,
never with a migration. A new view must be added to `ALLOWED_VIEWS` *and* to the view list inside
`SYSTEM_PROMPT` ([ai/context.py:20](../src/safwa/ai/context.py:20)) or the model cannot use it. The
same function drops the retired `card_search` FTS5 table and its triggers from older databases.

Saved Requests reuse the same validator plus two extra rules (`normalize_request_sql`): the query must
mention `ai_cards` and return a column named `id`.

### History — Telegram is the canonical dialogue store

[history.py](../src/safwa/history.py) rereads the real private chat through Telethon on **every** advisor
turn. `telegram_messages` stores only `(chat_id, message_id, direction, kind, related_id)` — never persona text.

- Every bot message must be registered with a `MessageKind` (`send_registered` / `register_message`).
  Unregistered outgoing = invisible to the LLM; wrongly-kinded = UI noise leaks into persona history.
- Only `DIALOGUE_USER`, `DIALOGUE_ASSISTANT`, `REMINDER`, `SUMMARY`, `SESSION_START`, and
  `SUBSESSION_RESULT` become dialogue. Everything else (`COMMAND`, `UI_INPUT`, `DASHBOARD`,
  `CARD_EDITOR`, `APPROVAL`, `RECEIPT`, `RETROSPECTIVE_PNG`, `ERROR`) is excluded.
- **Boundary required**: a visible `/newsession <request>` or the nearest `📜 Summary`. Without one,
  `recent(..., require_boundary=True)` raises `HistoryBoundaryMissing`. A `/newsession` always `break`s
  the scan; a Summary boundary is followed by up to `SUMMARY_CONTEXT_MESSAGE_LIMIT = 20` older messages
  carrying short UTC timestamps.
- The middleware deletes every slash command except `/newsession` (it must stay visible as the boundary).
- Bot API and Telethon use different message-ID spaces in a private chat. `_registered_message`
  correlates exactly-once: by ID first, then by ±15 s timestamp (ties prefer the larger Bot API ID).
  Keep that pairing intact.
- `dialogue()` merges consecutive human/user-side items into one `user` turn with `[User]`, `[Summary]`,
  `[Initial request]`, `[Subsession result]` tags; Safwa replies use the `assistant` role.

### Sessions, summaries, memory

- `/newsession <request>` sets the boundary. `/endsession [instruction]` confirms, then
  `compress_subsession` writes one `📦 Subsession request` result, the branch is deleted from Telegram,
  and the result stays as canonical user-side context.
- `PersonaContinuity.maybe_summarize` fires after an ordinary exchange once unsummarized dialogue
  reaches `summary_trigger_tokens` (10 000, ~3 chars/token estimate) and posts a `📜 Summary`.
- **`data/memory.md` is authoritative**: UTF-8, one non-empty fact per line, ~4 000-token budget.
  `memory_fact_cache` is a disposable mirror. AI writes go through `replace_facts`, which re-checks the
  file hash **before and after** writing a temp file, then `os.replace`s — a concurrent local edit is
  preserved, not overwritten. An invalid/oversized file disables injection instead of failing the turn;
  a missing file intentionally clears memory.
- `maintain_memory` retells ~2K-token chunks (500-token overlap), reconciles the fact list, writes
  atomically, and only then advances `processed_message_id`.
- `/setmemtime HH:MM|off` gates one automatic run per local calendar day
  (`run_due_memory_maintenance`, checked once a minute, skipped while foreground is busy).

### Reminders and retrospectives

- Deterministic first: `ReminderPolicy.candidates` computes eligible candidates
  (quiet hours, weekend flag, `proactive_limit` daily cap counted from `telegram_messages`, cooldown,
  dedupe key, global/kind snooze). `run_scheduler` takes **the first** candidate, and only then does the
  LLM decide `{"send":bool,"message":str}` — it never chooses *what* to remind about. Dedupe keys are
  always strings (`_card_key`), and the whole loop body is guarded so one failure cannot end it.
- Card-counting candidates (`today`, `planning`, `capacity`) filter to Actions, matching the dashboards.
- Candidate kinds: `morning`, `evening`, `feedback`, `today`, `stale_today`, `capacity`,
  `repeat_drift`, `inactivity`, `sprint_midpoint`, `sprint_end`, `blocked`, `value_neglected`, `planning`.
- Retrospective PNG: Matplotlib `Agg`, 3 axes (sprint effort bars — initial/added/removed/done/
  cancelled; committed-vs-completed by overlapping category; same by overlapping energy) plus
  `retrospective_recommendations` text derived from capacity, scope churn, Hard Time slippage, liked
  feedback, blocked work, and active Values. No "remaining" figure is stored or plotted.

### Concurrency and safety

- `GenerationGuard` — a single foreground lease keyed by the source `message_id`. While held, callbacks
  are rejected with an alert and new messages are deleted; `/cancel` and `/newsession` bypass it
  (`/newsession` calls `guard.cancel()`, bumping `dialogue_revision`).
- `dialogue.ordinary_text` captures `dialogue_revision` and `workspace.revision` before generating and
  discards the answer if either changed.
- `OwnerAndWritingMiddleware` drops anything that is not the owner in a private chat.
- `recover_startup` reconciles interrupted `agent_runs`, `resuming` approval batches, expired proposals,
  callback tokens, UI sessions, and stuck scheduled jobs on every boot.

## Schema

`models.py` is the **only** schema source. Startup calls `upgrade_database` = `Base.metadata.create_all`.
`create_all` adds missing tables/indexes and **never alters an existing one**, so a fresh database always
matches `models.py` while a changed column will *not* touch an existing `data/safwa.db`. A schema change
means editing `models.py` and rebuilding the database (`uv run safwa-backup` first).

**Do not add Alembic or write migrations.** Pre-release; the owner recreates the database.

30 tables: `workspace`, `user_profile`, `values`, `cards`, `card_values`, `tags`, `card_tags`,
`checks`, `check_values`, `check_tags`,
`saved_requests`, `card_categories`, `card_energy_types`, `sprints`, `sprint_commitments`, `card_events`,
`change_proposals`, `proposal_changes`, `agent_runs`, `agent_steps`, `telegram_messages`,
`feedback_queue`, `summary_state`, `memory_fact_cache`, `memory_sync_state`, `reminder_state`,
`scheduled_jobs`, `ui_sessions`, `callback_tokens`.

Enums are `StrEnum` but columns store plain strings — always compare/assign `.value`.

## Commands

```powershell
uv sync --extra dev
uv run safwa                 # run the bot (long polling)
uv run safwa-auth            # one-time Telethon user-session login
uv run safwa-qa-auth         # separate QA Telethon session
uv run safwa-backup          # zip: safwa.db + memory.md + manifest.json
uv run safwa-restore <zip> --yes
uv run pytest -q
uv run pytest tests\e2e -q
uv run pytest tests\e2e\live --live-telegram -q   # opt-in, needs SAFWA_QA_*
uv run ruff check .
```

`asyncio_mode = "auto"` — async tests need no marker. E2E tests use the real SQLite schema and real
services, replacing only Telegram and the provider at their network boundaries (`ScriptedProvider` in
`tests/e2e/conftest.py`). `resolve_qa_config` ([qa.py](../src/safwa/qa.py)) hard-fails on a reused bot
token or Telethon session path so QA can never touch production state.

## Conventions

- ruff `select = ["E","F","I","UP","B"]`, line length 100, `E501` ignored, target py312.
  Every module starts with `from __future__ import annotations`.
- Comments only for non-obvious *why* (Telegram/Telethon quirks, ordering constraints). Match that density.
- User-facing strings are complete sentences; "Card", "Sprint", "Value", "Tag", "Request" are capitalized
  domain nouns.
- Commit subjects: `vX.Y <short summary>`.
- `telegram-bot-exampler/` is an untracked local reference project excluded from ruff — never edit it.
