# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
It carries the principles only. Every mechanism has a document that owns it, listed under
[Where the detail lives](#where-the-detail-lives).

## Read first

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/STRUCTURE_GRAPH.md](docs/STRUCTURE_GRAPH.md)
and [docs/MEMORY_HISTORY_USAGE.md](docs/MEMORY_HISTORY_USAGE.md) at the start of a session — they are
the fastest path to full context.

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
If something is unclear, stop. Name what's confusing. Ask.
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
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

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

Single-owner Telegram bot (aiogram 3) + an OpenAI-compatible LLM (`SAFWA_AI_PROVIDER`, LM Studio by
default, OpenRouter for `openai/gpt-5.6-luna`) + SQLite/SQLAlchemy 2 async. Flat modules under
`src/safwa/`, wired in [main.py](src/safwa/main.py): `Settings` → `Database` →
provider/memory/advisor → `Services` dataclass injected as `dispatcher["services"]`, plus background
`asyncio` tasks cancelled in the polling `finally`.

Layer responsibilities are flat files: [domain.py](src/safwa/domain.py) (invariants + all mutations),
[telegram/](src/safwa/telegram) (all UI), [ai/service.py](src/safwa/ai/service.py) (agent loop +
proposals). Neither the UI nor the AI touches an ORM entity directly — both go through `domain.py`.

`docs/INITIAL_PLAN.md` and `docs/MEMORY_HISTORY_USAGE.md` are the authoritative product spec —
read them before changing history, memory, proposal, or UI behavior. When sources drift: the product
spec says what should happen, code and tests say what happens now, and descriptive docs explain the
current design. Do not present an unimplemented spec item as current behavior.

Every limit, budget, cap, interval, and the effort scale live in
[constants.py](src/safwa/constants.py), which imports nothing from Safwa;
[config.py](src/safwa/config.py) takes its defaults from there. Put a new tuning number there, not
next to its use site.

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
- Only dialogue, Reminders and Summaries become dialogue. Everything else — screens, receipts,
  errors, transient status — is excluded by its kind.
- The window is a **token budget**, not a message count, and a Summary is written exactly when it
  fills.
- Owner text that is still in the chat **is** dialogue: commands and typed field values are deleted,
  so survival is the evidence.
- Words that never reached the chat as owner text — a transcript, a drained queue — are posted back
  as a bot message of the owner's kind, or the advisor never sees them.

### AI mutations are always proposals

The model never mutates and never writes mutation SQL. A mutation tool call becomes a Pydantic
contract, then `ChangePreparer.prepare` against live data, then proposal rows, then a review screen,
and `ProposalService.apply` calls the *same* `domain.py` functions the manual UI calls.

- **Every mutation tool belongs to a subagent, never to the Advisor.** `board` owns the planning
  data, `diary` owns the Diary. Preparation runs where the change was authored.
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

The Advisor is a session ([`AgentSession`](src/safwa/ai/service.py)); a subagent is a session of the
same shape, reading the same conversation under its own prompt and its own tools. `route(name)` is a
call that returns: the subagent runs, its proposal is the screen, and what comes back to the caller
is a receipt — `did`, `text`, `error`. Only the Advisor writes to the chat, and the turn ends only
when the Advisor answers, so a request naming two domains is two routes and one message. A routed
subagent has no `route`, so there is no recursion.

- A session is its `agent_runs` row. `state_json` carries the dialogue, transcript, budget and
  receipts, so a suspended turn resumes from its own record rather than from the screen that
  suspended it, and `claimed_at` is what stops two resumes of the same session.
- A subagent reads that conversation as **data**: the newest `SUBAGENT_HISTORY_LAST_MESSAGES` come
  as one `<Conversation>` block, a tag per author, so nothing it did not write reaches it in the
  `assistant` slot — prose there demonstrates answering in prose. The Advisor is that conversation's
  assistant and reads the roles as they are. A routed session is also required to open with a tool
  call; only its first turn, because the loop ends on a turn that calls none.
- `parent_run_id` is who routed here. A screen suspends the whole chain; Save resumes the subagent,
  and its receipt resumes its caller, up to the session that has no parent.
- A session runs until it answers in words: a turn that stops with nothing is told so and asked
  again, bounded by the repair rounds. The session that writes to the chat is what guarantees the
  owner sees something — the receipts, or one `⚠️` line.
- Approve and Discard resume that session directly. Words typed over the screen do not: the screen
  freezes, the session is saved, and the Advisor takes the words — so a correction reaches the session
  that wrote the refused proposal. It is saved for **one Advisor turn**: a `route` back on that turn
  restores it, and anything else the Advisor does abandons it. The grace is the subagent's alone — a
  caller interrupted mid-route is cancelled with the words that interrupted it.
- The routing rules are prose in `SYSTEM_PROMPT`. A new subagent must be added *both* to the roster
  in [main.py](src/safwa/main.py) *and* to that section, or it is never routed to.
- A subagent **owns the writes** of its feature, never the reads. The Advisor reads every `ai_*`
  view, `ai_diary` included, and cites a day as `[16.08.2026](diary:12)`; the `diary` tool belongs to
  its subagent, and the Advisor holds no mutation tool at all.

### Read-only SQL is triple-guarded

`query_safwa` and saved Requests accept one `SELECT`/`WITH … SELECT` over the `ai_*` views only,
behind regex validation, a separate read-only connection with an authorizer allowlist, and result
caps ([ai/sql.py](src/safwa/ai/sql.py)).

The views are dropped and rebuilt on **every startup** — change view shape there, never with a
migration. A new view has to be added to `ALLOWED_VIEWS` *and* to the view list of every prompt that
should reach it; the prompt is what scopes a reader.

### `data/memory.md` is authoritative

[memory.py](src/safwa/memory.py): UTF-8, one non-empty fact per line. The `memory_fact_cache` table
is a disposable mirror — never treat it as the source. AI writes are atomic and re-check the file
hash, so a concurrent local edit is preserved rather than overwritten. An invalid or oversized file
disables memory injection instead of failing the turn.

### The prompt prefix must stay byte-stable

`_context_messages` ([ai/service.py](src/safwa/ai/service.py)) orders context blocks by how often
they change, so a remote provider can cache the prefix. **New volatile context goes after the
dialogue, never into a system block** — one timestamp in `messages[0]` costs every cache hit and
scatters OpenRouter's sticky provider routing.

Only `messages[0]` is a system message. Any other context block goes through `_system_note`, which
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
  Goal/Idea and has no children. Action-only fields are stripped for Goal/Idea at both the AI and the
  domain boundary.
- A Check records a state observation, never planned work: no effort, never in a Sprint, and Pending
  is derived rather than stored.
- A Card owns three link sets of one shape — Values, Tags, Checks. Adding a fourth means adding a
  spec, not a special case.
- `manual_stage` is what the user set; `effective_stage` is derived for parents from descendants and
  is what dashboards and queries read.
- Effort is restricted to `EFFORT_POINTS` and required for Actions; the `Literal` in
  `ai/contracts.py` mirrors it — change both together.
- Enums are `StrEnum` but columns store plain strings — always compare/assign `.value`.
- Entities carry a `version`, and `workspace.revision` is what a pending proposal is checked against
  before it applies. `StaleStateError` is the expected failure. Only `dialogue_revision` invalidates
  an in-flight answer, because the answer's own autoapproved change moves `workspace.revision`.
- Reminders are deterministic first: the scheduler only does schedule arithmetic, and the advisor
  composes the message. Safwa sends a proactive message only because a Reminder fired.

## Schema gotcha

There are no migrations and no Alembic. `models.py` is the only schema source: startup calls
`upgrade_database` ([db.py](src/safwa/db.py)), which is `Base.metadata.create_all`.

`create_all` adds missing tables and indexes and **never alters an existing one**, so a **fresh**
database always matches `models.py`, while adding or changing a column will **not** touch an existing
`data/safwa.db`. A schema change therefore means editing `models.py` and rebuilding the database
(back it up first with `uv run safwa-backup`).

**Do not add Alembic or write migrations before the first release.** The owner recreates the
pre-release database. Migration support starts after v1; its baseline is generated from `models.py`
at that point.

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
| Feature → file orientation | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Module and entity index | [docs/STRUCTURE_GRAPH.md](docs/STRUCTURE_GRAPH.md) |
| Product spec | [docs/INITIAL_PLAN.md](docs/INITIAL_PLAN.md) |
| History, memory, summaries | [docs/MEMORY_HISTORY_USAGE.md](docs/MEMORY_HISTORY_USAGE.md) |
| Checks | [docs/CHECKS_PLAN.md](docs/CHECKS_PLAN.md) |
| Diary | [docs/DIARY_PLAN.md](docs/DIARY_PLAN.md) |
| Reminders | [docs/REMINDERS_PLAN.md](docs/REMINDERS_PLAN.md) |
| Voice input | [docs/ASR_PLAN.md](docs/ASR_PLAN.md) |
