# Safwa — Architecture

Fast orientation map for a new session. Companion: [STRUCTURE_GRAPH.md](STRUCTURE_GRAPH.md)
(module/entity index), [INITIAL_PLAN.md](INITIAL_PLAN.md) + [MEMORY_HISTORY_USAGE.md](MEMORY_HISTORY_USAGE.md)
(product spec), [INCONSISTENCIES.md](INCONSISTENCIES.md) (spec vs code drift).

## What it is

Single-owner personal agile advisor. One private Telegram chat, aiogram 3 long polling, a local
OpenAI-compatible LLM (`SAFWA_AI_PROVIDER`: LM Studio by default, OpenRouter for
`openai/gpt-5.6-luna`), SQLite/SQLAlchemy 2 async. Runs on Windows, `uv`-managed,
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
8. `set_my_commands` (19 commands)
9. three background tasks, all cancelled in the polling `finally`:
   - `memory.poll(memory_error)` — 5 s `memory.md` hash watcher
   - `run_scheduler(..., gate/evaluate/escalate from `ReminderRuntime`)` — 30 s Reminder poll,
     `SAFWA_SCHEDULER_ENABLED` (on by default)
   - `run_memory_maintenance(...)` — 60 s daily-memory-sync eligibility loop

Memory maintenance stands down while `guard.active`; the Reminder poll has its own wider gate
(`ReminderRuntime.can_escalate`) that also waits out any open proposal.

## Layering

Flat modules under `src/safwa/` plus two packages (`ai/`, `telegram/`) — one file per responsibility,
not one package per layer:

| Responsibility | Files |
|---|---|
| domain | [domain.py](../src/safwa/domain.py), [enums.py](../src/safwa/enums.py), [models.py](../src/safwa/models.py), [saved_requests.py](../src/safwa/saved_requests.py), [reminders.py](../src/safwa/reminders.py) |
| application | [domain.py](../src/safwa/domain.py) (mutations), [ai/service.py](../src/safwa/ai/service.py) (`ProposalService`), [continuity.py](../src/safwa/continuity.py), [scheduler.py](../src/safwa/scheduler.py), [analytics.py](../src/safwa/analytics.py) |
| infrastructure | [db.py](../src/safwa/db.py), [history.py](../src/safwa/history.py), [memory.py](../src/safwa/memory.py), [ai/provider.py](../src/safwa/ai/provider.py), [ai/sql.py](../src/safwa/ai/sql.py), [backup.py](../src/safwa/backup.py) |
| telegram | [telegram/](../src/safwa/telegram) (13 modules, ~5.4k lines) |
| ai | [ai/](../src/safwa/ai) (context, contracts, mini, provider, reminder_sessions, service, sql) |
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
cards.py  checks.py  items.py    render modules
   ↑
screens.py  proposals.py    what one AI turn shows: cited items, the review screen
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
- **Card links**: a Card owns exactly three, all the same shape — Values, Tags, Checks. Each has a
  `ReferenceSpec` in `CARD_REFERENCE_SPECS`, a `toggle_card_*` command, and a `card_*` junction table;
  `ReferenceSpec.name_attr` is the only difference (a Check is named by `title`). Values have `active`
  (AI focus); Tags do not. Neither classifies a Check.
- **Checks**: a state observation ("did this hold?"), not planned work — no effort, never in a Sprint.
  A Card is its only relationship, held on the Card side in `card_checks`: one Check may hang on many
  Cards (one answer then satisfies all of them) or on none. A Check carries only a title and
  `repeatable`. `outcome` is `passed|missed`;
  **Pending is derived** (`outcome IS NULL`) and never stored, so there is no reset path. `resolved_at`
  keeps the *first* resolution — re-answering overwrites the outcome and the previous one is not
  retained. `series_id`/`source_instance_id` mirror the Card repeat lineage. A repeatable Check spawns a
  Pending successor **only on the Pending → resolved transition**, linked to its live Cards alone; if
  every linked Card is terminal or archived, the series ends there.
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
- Checks are item-shaped, not Card-shaped ([telegram/checks.py](../src/safwa/telegram/checks.py)) and
  are reached from the Card screen, which shows `☑️ Checks (pending/total)` **only when at least one
  Check hangs on the Card**. `render_check` also stands alone — an advisor link reaches a Check that
  hangs on no Card, and `card_id` then only decides where Back goes.
  Pressing `Done` on a gated Card opens the resolution screen instead of finishing it: each Check
  offers `✅ Passed` / `❌ Missed`, `Save` appears once an answer is set and finishes the Card in one
  transaction, and `Back` leaves it live. No answer is prefilled.
  A Check linked to no Card is reachable only through `ai_checks`; there is no `/checks` yet.
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

- Tools: one that runs immediately (`IMMEDIATE_TOOLS`) — `query_safwa(sql)` — plus the mutation tools
  `card`, `check`, `value`, `tag`, `request`, `remove` (`SAFWA_TOOLS`,
  [ai/service.py](../src/safwa/ai/service.py)).
- Offering an item is not a tool. The model cites it in its own prose as `[Milk](check:14)`, over the
  five openable types, and `render_citations`
  ([telegram/screens.py](../src/safwa/telegram/screens.py)) rewrites each citation of the escaped
  reply into `<a href="https://t.me/<bot>?start=check-14">`. The href is built from a validated id,
  never from the model, and the payload separator is `-` because `?start=` accepts only
  `[A-Za-z0-9_-]`. An id that is missing or archived keeps its words and loses its link: the reply is
  a `DIALOGUE_ASSISTANT` message that stays in the chat, so a dead link would stay with it.
- Tapping one sends `/start check-14`. The middleware deletes that command and `command_start`
  (`start_payload` → `open_citation` → `open_item_screen`) sends the item's manual screen as a new
  message, leaving the reply above it intact. A vanished item answers `⚠️ Error while opening: …`.
- The `card` tool writes **every** Card link — `value_*`, `tag_*`, `check_*`, one relationship group per
  `link`/`unlink` call. The `check` tool only creates, edits and answers a Check; it never attaches one.
  Since the UI cannot create, rename or (un)link a Check, those paths exist only here.
  `check_query` resolves an exact Check title, so a Check can be attached without knowing its id.
- `_guard_pending_checks` refuses to *prepare* a completion while Pending Checks exist, returning a
  retryable `ToolPreparationError` that carries their ids **and titles** so the model does not spend a
  `query_safwa` round finding them. The model then proposes `check(mode="complete"|"cancel")` for an
  answer the owner already gave, or cites the Check so they answer it themselves.
- **The model never mutates and never writes mutation SQL.** Tool call → Pydantic model in
  [ai/contracts.py](../src/safwa/ai/contracts.py) → `AgentChange` → `ChangeProposal` + `ProposalChange`
  rows → a read-only review screen with only **Save**/**Discard** → `ProposalService.apply` calls the
  *same* `domain.py` functions the manual UI calls.
- Multiple mutation calls in one turn become independent queued proposal screens in call order; the
  queue lives in an `AgentStep` row with `kind="approval_batch"`. The model resumes only after the last
  item resolves (`resolve_approval` → `continue_agent_approval`) and receives all mutation and read results.
- The batch also stores the request's `dialogue` and its `transcript` — every assistant/tool message the
  run produced after the context prefix. A resume rebuilds only the prefix (system prompt, planning state,
  memory, clock) and replays that transcript, so a request keeps its own intermediate steps across each
  approval instead of re-planning from the last tool call. It never re-reads Telegram for this.
- An empty provider turn is read by kind ([ai/provider.py](../src/safwa/ai/provider.py) `_read_turn`).
  No `choices` at all, or a choice cut off (`finish_reason` other than `stop`), is an upstream
  failure: retried once (`AI_EMPTY_RESPONSE_ATTEMPTS`), then raised carrying the provider's own
  reason. A `stop` with no content is the model deliberately adding nothing and is a valid turn —
  `_run_agent_loop(allow_silence=True)` accepts it after an approval queue, where the receipts are
  the answer; elsewhere it still raises `The advisor finished without a response`.
- Failed preparations return structured `ToolPreparationError` results and are retried for at most
  `MAX_REPAIR_ROUNDS = 5`; `MAX_TOOL_CALLS = 64`.
- A resolved queue item comes back as its own tool result carrying `status`, `entity`, `action`,
  `summary`, the resolved `fields`, and a `next` instruction; the summaries the owner sees never enter
  canonical history, summaries, or memory. `_assistant_content_with_request_progress` now only serves
  batches suspended before transcripts were persisted.
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
Pending is a null column. It is also the only route to a Check linked to no Card. The other direction
needs no join view: `ai_cards` carries `direct_checks` (titles) and `pending_checks` (count).

Views are dropped and rebuilt by `create_ai_views` **on every startup** — change view shape there,
never with a migration. A new view must be added to `ALLOWED_VIEWS` *and* to the view list inside
`SYSTEM_PROMPT` ([ai/context.py:20](../src/safwa/ai/context.py:20)) or the model cannot use it. The
same function drops the `card_search` FTS5 table and its triggers from older databases.

Saved Requests reuse the same validator plus two extra rules (`normalize_request_sql`): the query must
mention `ai_cards` and return a column named `id`.

### History — Telegram is the canonical dialogue store

[history.py](../src/safwa/history.py) rereads the real private chat through Telethon on **every** advisor
turn. `telegram_messages` stores only `(chat_id, message_id, direction, kind, related_id)` — never persona text.

- Every bot message must be registered with a `MessageKind` (`send_registered` / `register_message`).
  Unregistered outgoing = invisible to the LLM; wrongly-kinded = UI noise leaks into persona history.
- The kind is *also* written into the Telegram message itself: `mark_kind` appends five invisible
  characters encoding the `MessageKind`, and `read_kind_mark` recovers it. `telegram_messages` is
  therefore a cache, not the only copy — a rebuilt database still reads the whole dialogue back.
  Every bot send site must mark its text; the four outside `_messaging.py` are `main.memory_error`,
  `main.memory_error`, `dialogue.send_summary`, and the `/retro` caption. Codes in
  `_KIND_MARK_CODES` are append-only. Owner messages cannot be marked, so an unregistered owner
  message inside the session boundary is treated as dialogue; without a boundary it is dropped.
  A bot message with neither a registration nor a mark (anything predating marks) is read the same
  provisional way when it carries no inline keyboard — screens keep their buttons and stay excluded.
- Item citations ride in the text the same way. Telethon returns plain text, so `restore_citations`
  rewrites each `?start=<type>-<id>` link entity back into the `[Milk](check:14)` the model wrote;
  otherwise the model rereads its own citations as bare words and unlearns the format. Entity offsets
  are UTF-16 units, so the slicing happens in surrogate space.
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

### Reminders

Safwa sends a proactive message **only** because a Reminder the owner set fired. There are no computed
nudge kinds. A Reminder is instruction text plus a schedule — see
[REMINDERS_PLAN.md](REMINDERS_PLAN.md) for the full contract.

- The 30 s poll *is* the alarm clock. `Reminder.next_fire_at` is the only column it reads, and it is
  advanced **only after an escalation succeeds** — which is why a cancelled or crashed turn loses
  nothing: the row is still overdue, so the next tick retries it.
- The system's only output is an **escalation**: the instruction text is handed to the main advisor as
  a request (`format_escalation`, `dialogue=None`) and the advisor answers with the tools it already
  has. The reminder system never composes a message or renders an item.
- Two mini-sessions ([ai/mini.py](../src/safwa/ai/mini.py),
  [ai/reminder_sessions.py](../src/safwa/ai/reminder_sessions.py)), neither of which writes anything:
  **setup** resolves free-text timing into parameters before the proposal row exists, so the review
  screen shows a real schedule; **relevance** reads the `#id`s the instruction names and reports their
  state plus a `trigger`/`irrelevant` verdict. Both verdicts escalate.
- Two exact skips keep the relevance session off most fires: no `#id` in the instruction (nothing to
  look up), or `workspace.revision` unchanged since the cached verdict. The second holds only because
  advancing `next_fire_at` is written directly and does **not** bump the revision.
- Schedule arithmetic is one pure module ([reminders.py](../src/safwa/reminders.py)): `resolve`,
  `next_fire`, `roll_forward`, `describe`. `date` is always a start date; `time` is a fire clock when
  weekdays are given and a start clock otherwise.
- The owner always wins. `GenerationGuard` records whether the holder is the owner or a background
  escalation; an owner event cancels a background one rather than being deleted.

### Retrospectives

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
  callback tokens, UI sessions, and Reminder schedules on every boot. `reconcile_reminders` rolls a
  repeat forward only past `REMINDER_CATCHUP_GRACE_MINUTES` — an in-grace overdue row is left alone
  because the first poll firing it *is* the catch-up — and rebuilds wall clocks after a timezone move.

## Schema

`models.py` is the **only** schema source. Startup calls `upgrade_database` = `Base.metadata.create_all`.
`create_all` adds missing tables/indexes and **never alters an existing one**, so a fresh database always
matches `models.py` while a changed column will *not* touch an existing `data/safwa.db`. A schema change
means editing `models.py` and rebuilding the database (`uv run safwa-backup` first).

**Do not add Alembic or write migrations.** Pre-release; the owner recreates the database.

27 tables: `workspace`, `user_profile`, `values`, `cards`, `card_values`, `tags`, `card_tags`,
`checks`, `card_checks`,
`saved_requests`, `card_categories`, `card_energy_types`, `sprints`, `sprint_commitments`, `card_events`,
`change_proposals`, `proposal_changes`, `agent_runs`, `agent_steps`, `telegram_messages`,
`feedback_queue`, `summary_state`, `memory_fact_cache`, `memory_sync_state`, `reminders`,
`ui_sessions`, `callback_tokens`.

Enums are `StrEnum` but columns store plain strings — always compare/assign `.value`.

SQLite has no time zone type, so `DateTime(timezone=True)` accepts an aware value and returns a naive
one. `UtcDateTime` ([models.py](../src/safwa/models.py)) is a `TypeDecorator` that always reads back
tz-aware UTC; the Reminder datetime columns use it because that table is nothing but datetime
arithmetic. It emits identical DDL, so it is not a schema change.

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
