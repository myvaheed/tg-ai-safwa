# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
It carries the principles only. Every mechanism has a document that owns it, listed under
[Where the detail lives](#where-the-detail-lives).

## Read first

`tests/brd/*.feature` is what Safwa does, one approved rule per `Scenario`. Read the ones for the
feature you are changing before you change it, and [tests/brd/README.md](tests/brd/README.md) for
what a scenario is.

Then [docs/FEATURE_MODULES.md](docs/FEATURE_MODULES.md) for how a feature is wired in, and
[docs/AGENT_ARCH.md](docs/AGENT_ARCH.md) for sessions, routing and history. `uv run python
scripts/architecture_metrics.py` prints the current module graph — no document is kept in step
with it.

## Designing and Coding principles

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

Tradeoff: These guidelines bias toward caution over speed. For trivial tasks, use judgment.

**Simple is the test of correct.** A right solution is simple. When it is not simple, something is
wrong — go back and find it instead of building around it. A hard problem's right solution is a
composition of simple modular ones, never one complex whole: a monolithic complex solution is a
wrong solution. Several mechanisms that all compensate for one missing property are the signal.

1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, name what's confusing. Ask when the answer changes what you build;
otherwise state the assumption and keep going.
2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Match the surrounding style, unless replacing it is the change you were asked for.
Remove imports/variables/functions your changes made unused.
The test: every changed line traces to the request — and for a declared refactoring batch, to that
batch's stated scope. A batch declared to replace a mechanism deletes the old one, the workarounds
that compensated for it, and the vocabulary only it read. Dead code outside that scope is named,
not deleted.

4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

These guidelines are working if: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Commands

Windows / PowerShell, `uv`-managed. Python is pinned to `>=3.12,<3.13`.

```powershell
uv sync --extra dev
uv sync --extra asr-local    # optional: offline voice input (faster-whisper)
uv sync --extra asr-cuda     # the same plus the CUDA runtime wheels
uv run safwa                 # run the bot (long polling); creates missing SQLite tables first
uv run safwa-auth            # one-time Telethon user-session login (history reader)
uv run pytest -q
uv run pytest tests\e2e -q
uv run ruff check .
uv run python scripts/architecture_metrics.py   # migration rules and Definition of Done metrics
```

`tests/test_architecture.py` fails on a rule violation that is not in
`tests/architecture_allowlist.json`, and on a change to the prompt-prefix or schema snapshot under
`tests/snapshots/`. See [docs/MIGRATION.md](docs/MIGRATION.md).

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

Single-owner Telegram bot (aiogram 3) + an OpenAI-compatible LLM (`SAFWA_AI_PROVIDER`, LM Studio by
default, OpenRouter for `openai/gpt-5.6-luna`) + SQLite/SQLAlchemy 2 async. Flat modules under
`src/safwa/`, wired in [main.py](src/safwa/main.py): `Settings` → `Database` →
provider/memory/advisor → `Services` dataclass injected as `dispatcher["services"]`, plus background
`asyncio` tasks cancelled in the polling `finally`.

What each feature plugs into the application is declared once, in
[bootstrap/modules.py](src/safwa/bootstrap/modules.py) — see
[docs/FEATURE_MODULES.md](docs/FEATURE_MODULES.md).

The codebase is moving from flat layers to vertical features. A feature owns its model, use cases,
agent contract and Telegram adapter, and its AI and UI mutation paths call the same operations —
[features/diary](src/safwa/features/diary) is the shape to copy. What has not moved yet lives in
[domain.py](src/safwa/domain.py) and [telegram/](src/safwa/telegram) until its declared phase.

### Board and Planning are not the same word

**One package per `.feature` file**, so a rule and the code that keeps it are found in one place.

- **The board** is what the owner keeps: Cards, Checks, Values, Tags, Requests and Reminders — the
  set, not one entity. `board` is the subagent that proposes every change to it, and
  [features/board](src/safwa/features/board) is that subagent and nothing else: the roster lets it
  declare mutation tools the features that own those entities publish.
- **Planning** is the workspace mode without a running Sprint (`WorkspaceMode.PLANNING`), the Sprint
  itself, and the screen where the next one is planned.
  [features/planning](src/safwa/features/planning) is exactly that and nothing else.

A Card is not "planning data". Say Card, Check, Value, Tag, Sprint — or say the board.

`AgentSpec.board_state` is that set's current state, sent to a subagent that asked for it, and the
model reads it under that name. The `Workspace mode:` line inside it is the other word: there
`planning` is the mode with no Sprint.

**`tests/brd/*.feature` is the product spec.** An approved scenario outranks the code, the tests
and every document, and changing one needs the owner. `archived_docs/` is not a spec: it was written
quickly, and it describes modules that no longer exist. Read it for what a rule was getting at, check
that against the code, and write down what you found — never quote it as current behavior.

Cross-feature tuning — token budgets, poll intervals, shared timeouts — lives in
[constants.py](src/safwa/constants.py), which imports nothing from Safwa;
[config.py](src/safwa/config.py) takes its defaults from there. A limit that belongs to one feature
is a constant at the top of that feature's module, next to where it is used. The same split applies
to [enums.py](src/safwa/enums.py): `MessageKind` and `AIProvider` are shared, while `CardStage` and
its two sets now live in [features/cards/model.py](src/safwa/features/cards/model.py) and
`CheckOutcome` in [features/checks/model.py](src/safwa/features/checks/model.py). `CardKind` and
`WorkspaceMode` still wait in `enums.py`; `WorkspaceMode` belongs to Planning.

The `telegram` package is layered and imports run one way only: `_core.py` ← `_presentation.py` ←
`_messaging.py` ← `text_input.py` ← the feature renderers ← `screens.py` / `proposals.py` ← the
handlers. Only [commands.py](src/safwa/telegram/commands.py),
[callbacks.py](src/safwa/telegram/callbacks.py) and [dialogue.py](src/safwa/telegram/dialogue.py)
register `@router` handlers, and [__init__.py](src/safwa/telegram/__init__.py) imports them for that
side effect — dropping one silently unregisters its handlers. A leading underscore means
module-local: a name used by a sibling module carries no underscore, even though the whole package
stays private behind `__init__.__all__`.

## Principles

### Telegram is the canonical dialogue store, not SQLite

[history.py](src/safwa/history.py) re-reads the real private chat through Telethon on every advisor
turn; `telegram_messages` stores event metadata, never persona text.

- **Every bot message is sent registered and marked** with a `MessageKind`. An unregistered or
  unmarked message is invisible to the LLM; a wrongly-kinded one leaks UI noise into persona history.
- Only dialogue, Cues and Summaries become dialogue. Everything else — screens, receipts,
  errors, transient status — is excluded by its kind.
- The window is a **token budget**, not a message count, and a Summary is written exactly when it
  fills.
- Owner text that is still in the chat **is** dialogue: commands and typed field values are deleted,
  so survival is the evidence.
- Words that never reached the chat as owner text — a transcript, a drained queue — are posted back
  as a bot message of the owner's kind, or the advisor never sees them.

### AI mutations are always proposals

The model never mutates and never writes mutation SQL. A mutation tool call becomes a Pydantic
contract, then `ChangePreparer.prepare` against live data, then an open review, then a review
screen, and `approve_proposal` calls the *same* `domain.py` functions the manual UI calls.
A review is process state, never a row: `ProposalStore` holds it, and a restart ends every one.

- **Every mutation tool belongs to a subagent, never to the Advisor.** `board` owns the board —
  Cards, Checks, Values, Tags, Requests, Reminders — and `diary` owns the Diary. Preparation runs where the change was authored.
- **Every proposal screen is exactly Save/Discard.** A screen that needs a field control is the wrong
  screen.
- Autoapproval decides only whether a screen is shown; it never bypasses proposal persistence, and
  any doubt or failure leaves the pending screen untouched.
- When the decision is the user's, the model **cites** the item in its own prose instead of proposing
  one. A citation link is built host-side from a validated id, never from model text, and an item
  that is gone keeps its words and loses its link.
- Several mutation calls in one turn queue as independent screens, and the model resumes only after
  the last one resolves, with every result handed back as a tool result.
- Do not mix an immediate tool and mutation tools in one provider response; runtime rejects the
  mutations and the model retries them after it has seen the read data.
- Anything the model must know across an approval belongs in a tool result, not in a receipt.

### A session is the unit, and `route` hands one turn to another

The Advisor is a session ([`AgentSession`](src/agent_runtime/model.py)); a subagent is a session of the
same shape, reading the same conversation under its own prompt and its own tools. `route(name)` is a
call that returns: the subagent runs, its proposal is the screen, and what comes back to the caller
is a receipt — `did`, `text`, `error`. Only the Advisor writes to the chat, and the turn ends only
when the Advisor answers, so a request naming two domains is two routes and one message. A routed
subagent has no `route`, so there is no recursion.

- A session is its `agent_runs` row. `state_json` carries the dialogue, transcript, budget and
  receipts, so a suspended turn resumes from its own record rather than from the screen that
  suspended it. A suspension hands back an opaque `InteractionRef`, and both halves of it guard the
  resume: `claimed_at` stops two resumes at once, and the token — minted at the checkpoint, cleared
  when it is taken — stops one screen being answered twice. Suspend, `resume` and `interrupt` all
  belong to `AgentManager`; Safwa keeps only the mapping from a screen to its reference.
- A subagent reads that conversation as **data**: the newest `SUBAGENT_HISTORY_LAST_MESSAGES` come
  as one `<Conversation>` block, a tag per author, so nothing it did not write reaches it in the
  `assistant` slot — prose there demonstrates answering in prose. The Advisor is that conversation's
  assistant and reads the roles as they are. A routed session is also required to open with a tool
  call; only its first turn, because the loop ends on a turn that calls none.
- `route` hands the turn to a subagent that **writes**. `call_helper` asks one that only **reads**
  and answers with rows: it cannot open a screen, so nothing suspends and the caller keeps its turn.
  A helper is in no routing rule and in no base tool set — the tool result that needed one is what
  offers it, and `heavy_analyzer` is the only one. It ends by forwarding its own last read, never by
  retelling it.
- `parent_run_id` is who routed here. A screen suspends the whole chain; Save resumes the subagent,
  and its receipt resumes its caller, up to the session that has no parent.
- A session runs until it answers in words: a turn that stops with nothing is told so and asked
  again, bounded by the repair rounds. The session that writes to the chat is what guarantees the
  owner sees something — the receipts, or one `⚠️` line.
- Approve and Discard resume that session directly. Words typed over the screen do not: every pending
  proposal in that batch is rejected, and the words **resume the turn that opened the screen** rather
  than starting a second one — its pending `route` is answered with what was proposed, what was
  refused, what was already saved, and the words themselves. A screen is never something the owner
  comes back to: a UI message is only ever the last message in the chat and never moves back up, so
  anything done below one interrupts it.
- An interruption leaves the subagent **unfinished, not finished**, so a `route` back on that same
  turn resumes it with its own plan — which is what makes "the same, but capitalise the name" a
  correction rather than a rewrite. Its own record says the owner refused *and wrote instead*, or it
  proposes the same thing again. The turn that routed there is the outer bound: when it answers or
  fails, it ends whatever it left unfinished.
- The routing rules in `SYSTEM_PROMPT` are generated from the roster, so a subagent is routed to
  exactly when its `AgentSpec` is in `MODULES`; its `purpose` **is** the prompt line.
- A subagent **owns the writes** of its feature, never the reads. The Advisor reads every `ai_*`
  view, `ai_diary` included, and cites a day as `[16.08.2026](diary:12)`; the `diary` tool belongs to
  its subagent, and the Advisor holds no mutation tool at all.

### Read-only SQL is triple-guarded

`query_safwa` accepts one `SELECT`/`WITH … SELECT` over the `ai_*` views only, behind regex
validation, a separate read-only connection with an authorizer allowlist, and result caps
([ai/sql.py](src/safwa/ai/sql.py)).

A saved Request shares the validation and nothing else: it runs on the ordinary session, and the
caps do not apply because its result is always a list in the interface and never enters the model's
history. Those caps exist because a local model pays for what it reads.

The views are dropped and rebuilt on **every startup** — change view shape in the owning feature's
`views.py`, never with a migration. `ALLOWED_VIEWS` and `CREATE VIEW` both come from those `SqlView`
declarations, and the composition root hands the catalogue to whoever validates against it. A
`SqlView` carries its own `doc` too, so the block a model reads about a view lives beside the SELECT.
**The view list in a prompt is what scopes a reader**: an agent names the views it reads, the
composition root fills them into `{views}`, and a view no list names is one that reader never learns
exists. The Diary writes its own list by hand, with columns trimmed on purpose.

### `data/memory.md` is authoritative

[features/continuity/memory.py](src/safwa/features/continuity/memory.py): ordinary UTF-8 text. Each
trimmed non-empty line is a fact, blank lines are ignored, and a missing file means empty memory. The
`memory_fact_cache` table is a rebuildable derived cache — never treat it as the source. AI replacements
write atomically and re-check the file hash so a concurrent local edit is preserved rather than
overwritten. A file that is not UTF-8, or that is over the token budget, injects no memory and
records why instead of failing the turn — the budget bounds what is read as well as what is written.

### The prompt prefix must stay byte-stable

`ContextBuilder` ([ai/messages.py](src/safwa/ai/messages.py)) orders context blocks by how often
they change, so a remote provider can cache the prefix. **New volatile context goes after the
dialogue, never into a system block** — one timestamp in `messages[0]` costs every cache hit and
scatters OpenRouter's sticky provider routing.

Only `messages[0]` is a system message. Any other context block goes through `system_note`, which
sends it as a user message prefixed `[System]: ` — the Qwen3.5 chat template raises on a second
system message.

### Concurrency and UI state

- `GenerationGuard` is the single foreground/background lease. While an answer runs, callbacks are
  rejected and owner text is queued, then processed as one turn. Background work verifies the
  revision before publishing or committing.
- `OwnerAndWritingMiddleware` drops anything that is not the owner in a private chat.
- Every inline button is a single-use `CallbackToken` row; `UiSession` holds transient editor state
  and manual creation persists nothing until Save.
- Bot messages are HTML — escape any user or model text.
- `recover_startup` ([recovery.py](src/safwa/recovery.py)) reconciles interrupted work on every boot.

### Domain invariants

- Card tree: Goal is root-only; Idea may be root or under a Goal; Action may be root or under
  Goal/Idea and has no children. **Stage**, effort, repeat, categories, energy and **Blocked**
  belong to an Action alone, and are stripped for Goal/Idea at both the AI and the domain boundary.
  A Card's parent is set by proposal only; no screen offers the control.
- A Goal and an Idea show what their **direct children** add up to. Each child already carries its
  own derived values, so the recursion reaches the Actions, and a child that never started still
  counts: an Idea with nothing in it is in Backlog and holds its Goal there.
  `propagate_ancestors` is the one walk that writes it, into the plain `effective_stage`, `blocked`,
  `effort_points` and `archived_at` columns, so Safwa reads one column that means the same thing on
  every row. Every path that changes an Action ends there. A parent with nothing under it shows Backlog and
  never Done or Cancelled, and it has no `blocked_description` of its own. Summing `effort_points`
  over every row counts each Action again inside every ancestor — a real total says
  `WHERE kind = 'action'`.
- `manual_stage` is what the user set, and it is an Action's alone; `effective_stage` is what
  dashboards and queries read.
- A Check records a state observation, never planned work: no effort, never in a Sprint, and Pending
  is derived rather than stored. A Check hangs on **one** Card or on none.
- Three rules govern a Check across a Card's life: a Card closes when every Check series on it was
  answered at least once **on this Card**; closing deletes whatever is still Pending; reopening puts
  each plain Check back to Pending and opens one fresh instance of each repeating series. They live
  in [features/checks](src/safwa/features/checks), and Cards reaches them through `checks/api.py`.
- **Everything is deleted; only a Card and a Check are also archived**, two Sprints after they
  closed (`ARCHIVE_AFTER_SPRINTS`). Archived is a matter of sight: it still counts everywhere it
  counted. A Value, a Tag and a Saved Request carry no `archived_at` at all.
- **Only an Action is archived; a Goal and an Idea are derived, like everything else they show.**
  A branch leaves sight when its last Card does and comes back the moment one is reopened, so a
  parent is never stamped, never restored and never carries a `card_events` row of its own.
- **A list by stage leaves an archived item out; every other list shows it, marked `[📦]`.** It
  opens, it reads as archived, and no proposal changes it — only the owner, by reopening or
  deleting it. `domain.title_marks` is the one place both marks are written, and `ai_cards` and
  `ai_checks` render the same wording in SQL.
- A Card owns three link sets of one shape — Values, Tags, Checks — and a Check owns one, its
  Values. All four are `ReferenceSpec`s: adding another means adding a spec, not a special case.
  A Check's Values are its own statement about what it measures; nothing is derived between them
  and the Values of the Cards that Check belongs to.
- Effort is restricted to `EFFORT_POINTS` and required for Actions; the `Literal` in
  `ai/contracts.py` mirrors it — change both together.
- Enums are `StrEnum` but columns store plain strings — always compare/assign `.value`.
- Entities carry a `version`, and `workspace.revision` is what a pending proposal is checked against
  before it applies. `StaleStateError` is the expected failure. Only `dialogue_revision` invalidates
  an in-flight answer, because the answer's own autoapproved change moves `workspace.revision`.
- Safwa speaks first only because a **Cue** was written for it — a Reminder that came due, a
  Sprint that ended. The producer writes the finished request into `cues` in its own transaction;
  `CueRuntime` owns the gate, the lease and the one turn, and deletes the row only once the owner
  has the words. That row is the single record of what Safwa still owes, so the Reminder poll
  does schedule arithmetic and nothing else, and one thing waits to be said at a time.

## Schema gotcha

There are no migrations and no Alembic. The ORM model modules are the schema source: startup calls
`upgrade_database` ([foundation/database.py](src/safwa/foundation/database.py)), which is
`Base.metadata.create_all`. During the vertical migration, [models.py](src/safwa/models.py) imports
feature-owned models so the metadata is complete before startup creates tables.

`create_all` adds missing tables and indexes and **never alters an existing one**, so a **fresh**
database always matches the declared models, while adding or changing a column will **not** touch an
existing `data/safwa.db`. A schema change therefore means editing the owning model and rebuilding the
database (back it up first with `uv run safwa-backup`).

**Do not add Alembic or write migrations before the first release.** The owner recreates the
pre-release database. Migration support starts after v1; its baseline is generated from the complete
ORM metadata at that point.

## Conventions

- ruff `select = ["E","F","I","UP","B"]`, line length 100, `E501` ignored, target py312. All modules
  start with `from __future__ import annotations`.
- Comments are used sparingly and only to explain non-obvious *why* (Telegram/Telethon quirks,
  ordering constraints). Match that density; do not add narrative comments.
- **Everything the model reads is written for a small local model — 4B to 12B.** System prompts,
  tool descriptions, field descriptions, `hint`, and `next` are short, imperative, and concrete:
  numbered or bulleted steps, one instruction per line, the exact tool and field names. No rationale,
  no reassurance, no restating a rule in a second way. Say a thing once, where it is used.
- Docs follow the same rule. Edit the fewest places that are actually wrong, and keep the edit as
  short as the line it replaces. Describe the behavior that exists now — never the design it
  replaced, why the old one was dropped, or how deliberate the new one is.
- User-facing strings are complete sentences and product-specific ("Card", "Sprint", "Value", "Tag",
  "Request" are capitalized domain nouns).
- Commit subjects in this repo follow `vX.Y <short summary>`.
- E2E tests use the real migrated SQLite database and real services, replacing only Telegram and the
  provider at their network boundaries. Keep new tests on that pattern rather than mocking domain
  functions.
- Never let QA/live test config touch production state: `resolve_qa_config`
  ([qa.py](src/safwa/qa.py)) hard-fails on a reused bot token or Telethon session path.

## Where the detail lives

| Subsystem | Document |
|---|---|
| Product spec | [tests/brd/](tests/brd) |
| What a scenario is | [tests/brd/README.md](tests/brd/README.md) |
| History, memory, summaries | [archived_docs/MEMORY_HISTORY_USAGE.md](archived_docs/MEMORY_HISTORY_USAGE.md) |
| Checks | [archived_docs/CHECKS_PLAN.md](archived_docs/CHECKS_PLAN.md) |
| Diary | [archived_docs/DIARY_PLAN.md](archived_docs/DIARY_PLAN.md) |
| Reminders | [archived_docs/REMINDERS_PLAN.md](archived_docs/REMINDERS_PLAN.md) |
| Subagents and routing | [archived_docs/SUBAGENTS_PLAN.md](archived_docs/SUBAGENTS_PLAN.md) |
| Voice input | [archived_docs/ASR_PLAN.md](archived_docs/ASR_PLAN.md) |
| Sessions, routing, helpers, cues, history | [docs/AGENT_ARCH.md](docs/AGENT_ARCH.md) |
| How a feature plugs in | [docs/FEATURE_MODULES.md](docs/FEATURE_MODULES.md) |
| LLM provider boundary | [docs/LLM_GATEWAY.md](docs/LLM_GATEWAY.md) |
| Clean-architecture migration | [REFACTORING_CLEAN_ARCH_FINAL.md](REFACTORING_CLEAN_ARCH_FINAL.md) |
| Migration status and gates | [docs/MIGRATION.md](docs/MIGRATION.md) |
| Approval packets, while the migration runs | [docs/brd/README.md](docs/brd/README.md) |

`archived_docs/` records intent, not the code: its file names and module lists are pre-migration and
largely wrong. `docs/brd/` is the approval packet around the scenarios and is deleted at the end of
the migration; `tests/brd/` survives it. `docs/` is where anything written from now on goes.

**Keep this file and `README.md` current by deleting, not by adding.** A line that stopped being
true is removed or replaced in place — never left standing next to its correction. Both files
name only things that exist; `tests/test_docs.py` checks every link they carry.
