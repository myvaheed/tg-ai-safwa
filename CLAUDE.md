# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
It carries the principles only. Every mechanism has a document that owns it, listed under
[Where the detail lives](#where-the-detail-lives).

## Read first

`tests/brd/*.feature` is what Safwa does, one approved rule per `Scenario`. Read the ones for the
feature you are changing before you change it, and [tests/brd/README.md](tests/brd/README.md) for
what a scenario is.

Then [docs/DOMAIN.md](docs/DOMAIN.md) for what a Card, a Check and a Sprint are,
[docs/FEATURE_MODULES.md](docs/FEATURE_MODULES.md) for how a feature is wired in, and
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
uv run python scripts/architecture_metrics.py   # the architecture rules and the module graph
```

Every architecture rule reads zero. `tests/test_architecture.py` fails on any violation, and on a
change to the prompt-prefix, schema or marker-code snapshot under `tests/snapshots/`. Rewrite a
snapshot only in a batch declared to change that artefact:

```powershell
uv run pytest tests/test_architecture.py --snapshot-update
```

Single test / single file:

```powershell
uv run pytest tests\test_cards.py::test_parent_stage_propagation_and_reopen -q
```

`asyncio_mode = "auto"`, so async tests need no marker. Live Telegram tests are opt-in and skipped
unless `--live-telegram` is passed (`uv run pytest tests\e2e\live --live-telegram -q`); they need a
separate BotFather bot configured through the `SAFWA_QA_*` variables plus `uv run safwa-qa-auth`.

Backup/restore CLIs: `uv run safwa-backup`, `uv run safwa-restore <zip> --yes`.

`telegram-bot-exampler/` is an untracked local reference project, excluded from ruff — never edit it.

## Architecture

Single-owner Telegram bot (aiogram 3) + an OpenAI-compatible LLM (`SAFWA_AI_PROVIDER`, LM Studio by
default, OpenRouter for `openai/gpt-5.6-luna`) + SQLite/SQLAlchemy 2 async. Wired in
[bootstrap/main.py](src/safwa/bootstrap/main.py): `Settings` → `Database` →
provider/memory/advisor → `Services` dataclass injected as `dispatcher["services"]`, plus background
`asyncio` tasks cancelled in the polling `finally`.

What each feature plugs into the application is declared once, in
[bootstrap/modules.py](src/safwa/bootstrap/modules.py) — see
[docs/FEATURE_MODULES.md](docs/FEATURE_MODULES.md).

A feature owns its model, use cases, agent contract and Telegram adapter, and its AI and UI
mutation paths call the same operations — [features/diary](src/safwa/features/diary) is the shape
to copy.

**`tests/brd/*.feature` is the product spec.** An approved scenario outranks the code, the tests
and every document, and changing one needs the owner. **One package per `.feature` file**, so a
rule and the code that keeps it are found together.

Where a shared thing goes is decided by how many features read it. Cross-feature tuning — token
budgets, poll intervals, shared timeouts — lives in [constants.py](src/safwa/constants.py), which
imports nothing from Safwa. A limit one module owns is a constant at the top of that module, next
to where it is used, and [config.py](src/safwa/config.py) takes a default from wherever the limit
lives. [enums.py](src/safwa/enums.py) splits the same way: what several features read is there,
what one feature owns lives with it.

[shell/](src/safwa/shell) is what a feature's Telegram adapter imports besides `telegram_llm`. Only
[shell/commands.py](src/safwa/shell/commands.py), [shell/callbacks.py](src/safwa/shell/callbacks.py)
and [turn/dialogue.py](src/safwa/turn/dialogue.py) register `@router` handlers, and each is imported
for that side effect alone — dropping one silently unregisters its handlers. A leading underscore
means module-local: a name a sibling module uses carries none, even though the package stays private
behind its `__init__`.

[ai/](src/safwa/ai) is the other application beside the shell: Safwa's agent engine — what a session
is, what a tool call costs, what the model may read. **It imports no feature**, and Rule M in
[scripts/architecture_metrics.py](scripts/architecture_metrics.py) keeps that true; every feature
imports it. The engine and [features/proposals](src/safwa/features/proposals) are meant to be
lifted into the next project together, so Rule N holds both to naming no Safwa entity: what they
may reach is the `PORTABLE_FOUNDATION` list, and the review flow's `telegram/` and `module.py` are
the port that stays behind. The session that composes the two is
[session.py](src/safwa/session.py), which names no feature either;
[features/advisor](src/safwa/features/advisor) owns the prompt and the views it is wired with, and
declares no `MODULE` because the composition root wires the root session directly.

## The rules that outrank a convenient design

Each is a property some mechanism exists to hold. Break one and the mechanism around it stops
meaning anything, so change the mechanism instead. [docs/AGENT_ARCH.md](docs/AGENT_ARCH.md) is how
each is held; [docs/DOMAIN.md](docs/DOMAIN.md) is what the words mean.

- **Telegram is the dialogue store, not SQLite.** Every bot message is sent registered and marked
  with a `MessageKind`, and its kind is the only thing that decides whether the model ever sees it.
  An unregistered or wrongly-kinded message is a silent bug weeks wide. Rule P keeps the send path
  single, and Rule O keeps a released `MARKS` code from ever meaning something else.
- **The model proposes; it never writes.** Every mutation tool belongs to a subagent, never to the
  Advisor, and Save calls the *same* use cases the manual UI calls. A proposal screen is exactly
  Save/Discard: a screen that needs a field control is the wrong screen.
- **A session is the unit, and only the Advisor writes to the chat.** `route` hands one turn to a
  subagent and gets a receipt back; a screen suspends the whole chain and a Save resumes it. A
  session runs until it answers in words.
- **A reader is scoped by the view list in its prompt.** `query_safwa` takes one read-only SELECT
  over the `ai_*` views, triple-guarded, and a view no list names is one that reader never learns
  exists. Views are rebuilt every startup from the owning feature's `views.py`, never migrated.
- **The prompt prefix is byte-stable.** New volatile context goes after the dialogue, never into a
  system block — one timestamp in `messages[0]` costs every cache hit.
- **`data/memory.md` is authoritative.** The `memory_fact_cache` table is a rebuildable derived
  cache; never treat it as the source.
- **One lease, and the owner always wins.** `TurnManager` is the single foreground/background lease;
  background work verifies the revision before it publishes or commits.

## Schema gotcha

There are no migrations and no Alembic. The ORM model modules are the schema source: startup calls
`upgrade_database` ([foundation/database.py](src/safwa/foundation/database.py)), which is
`Base.metadata.create_all`. Every table is declared by the module that owns it, and importing
[bootstrap/modules.py](src/safwa/bootstrap/modules.py) reaches all of them, so the metadata is
complete before startup creates tables.

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
  "Request" are capitalized domain nouns). Bot messages are HTML — escape any user or model text.
- Enums are `StrEnum` but columns store plain strings — always compare and assign `.value`.
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
| The domain and its invariants | [docs/DOMAIN.md](docs/DOMAIN.md) |
| The six flows, drawn | [docs/diagrams/](docs/diagrams) |
| History, memory, summaries | [telegram_history.feature](tests/brd/telegram_history.feature), [continuity.feature](tests/brd/continuity.feature), [features/continuity](src/safwa/features/continuity) |
| Checks | [checks.feature](tests/brd/checks.feature), [features/checks](src/safwa/features/checks) |
| Diary | [diary.feature](tests/brd/diary.feature), [features/diary](src/safwa/features/diary) |
| Reminders | [reminders.feature](tests/brd/reminders.feature), [features/reminders](src/safwa/features/reminders) |
| Voice input | [agents.feature](tests/brd/agents.feature), [adapters/asr.py](src/safwa/adapters/asr.py) |
| Sessions, routing, helpers, cues, history | [docs/AGENT_ARCH.md](docs/AGENT_ARCH.md) |
| How a feature plugs in | [docs/FEATURE_MODULES.md](docs/FEATURE_MODULES.md) |
| LLM provider boundary | [docs/LLM_GATEWAY.md](docs/LLM_GATEWAY.md) |

A feature's own package is a pointer like any other: its `.feature` file is the rule, and the
package is what keeps it. `docs/` is where anything written from now on goes.

**Keep every document current by deleting, not by adding.** A line that stopped being true is
removed or replaced in place — never left standing next to its correction. `tests/test_docs.py`
checks that each link resolves and each code name a document spells still exists, so a rename that
leaves the prose around it standing fails there.
