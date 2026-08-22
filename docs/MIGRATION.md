# Migration status

The plan is `REFACTORING_CLEAN_ARCH_FINAL.md`. This file records where the migration stands and
what the numbers were when it started. Every batch updates the table and the metrics.

## Gates

```bash
uv run pytest -q
uv run ruff check .
uv run python scripts/architecture_metrics.py
```

`tests/test_architecture.py` enforces rules A–K against `tests/architecture_allowlist.json`.

`tests/brd/*.feature` are approved traceability contracts, not a Behave suite. The unit and E2E
pytest tests cite their `DI-*` scenario and feature file in their docstrings.
Counts in that file may only fall. A rule that starts passing means the allowlist is stale and
its own test says so — regenerate it in the batch that fixed it.

Rules I and J are snapshots under `tests/snapshots/`: the prompt prefix and the declared schema.
Rewrite them only inside a batch declared to change that artefact:

```bash
uv run pytest tests/test_architecture.py --snapshot-update
```

## Baseline, recorded at the end of Phase 0

| | |
|---|---|
| Tests before Phase 0 | 457 passed, 3 skipped |
| Tests after Phase 0 | 496 passed, 3 skipped (427 test functions) |
| `ruff check .` | clean |
| Modules under `src/` | 53 |
| Entity dispatch points outside `features/` (DoD #1) | 75 |
| Use case base abstractions (DoD #2) | 0 |
| Modules over 600 lines (DoD #3) | 6 |
| Re-export-only modules (DoD #13) | 0 |
| Rule G violations | 2 (`asr.py`, `telegram/_messaging.py`) |
| Import cycles | 0 (274 edges) |

Largest modules: `ai/service.py` 3062, `domain.py` 1908, `telegram/callbacks.py` 1200,
`telegram/cards.py` 942, `telegram/commands.py` 689, `history.py` 654.

DoD #1 is the number to watch. It counts every dict, set, tuple, list or comparison outside
`features/` that fans out over entity names — that is what "adding an entity means editing nine
registries" looks like when a machine counts it. The target is one place, `bootstrap/modules.py`.

## Phases

| # | Phase | Kind | Status |
|---|---|---|---|
| 0 | Foundation and rules of the game | technical | **done** |
| 1 | `llm_gateway` | technical | **done** |
| 2 | `FeatureModule` and proposal capabilities | technical | **done** |
| 3 | Pilot: Diary | business | **done** |
| 4 | Leaf business batches | business | **in progress** — 4.a Continuity and Profile, 4.b Reminders, 4.c Saved Requests |
| 5 | Planning core | business | not started |
| 6 | Proposals and the first reactive process | business | not started |
| 7 | `agent_runtime` | technical + business | not started |
| 8 | `telegram_llm` and `TurnManager` | technical | not started |
| 9 | Packages and cleanup | technical | not started |

A phase ends in a state that can be kept forever: tests green, bot working, no old path running
beside a new one. A phase that cannot be finished is rolled back whole.

## What Phase 0 delivered

- `src/safwa/foundation/` — `database.py` (moved from `db.py`, plus `Database.transaction()`),
  `clock.py`, `state_flow.py`
- `tests/test_state_flow.py` — conflation, duplicate suppression, immediate `current`, subscriber
  cleanup, the read-only view, and the `map` / `filter` / `merge` / `combine` operators
- `tests/test_architecture.py` + `tests/architecture_allowlist.json` + `tests/snapshots/`
- `scripts/architecture_metrics.py`, `scripts/test_inventory.py`
- `docs/brd/README.md`, `docs/brd/test_inventory.md`
- CLAUDE.md: the constants rule from plan §10.4, and the archived-docs paths

`foundation/clock.py` has no consumer yet — `domain.utcnow` is still what production calls. Its
first consumer is the Diary pilot in Phase 3, which is where the first use case starts taking
time as a dependency. The `state_flow` operators are unconsumed for the same reason: the first
Manager arrives in Phase 6.

`map` and `combine` return a `StateFlow`, because a derived value that both sides can always
answer still has a `current`. `filter` and `merge` return a plain `AsyncIterator`: a filtered
stream has no current value when the current state fails the predicate, and two merged sources
have two currents and no single one. A `StateFlow` that cannot answer `current` is not one.

## What Phase 1 delivered

- `src/llm_gateway/` — neutral `CompletionRequest`, `CompletionTurn`, `ToolCall`, `Usage`, the
  `LlmProvider` protocol, `OpenAICompatibleProvider`, and `ScriptedProvider`
- Safwa's agent, mini-session, continuity, reminder, and bootstrap consumers now depend only on
  `LlmProvider`; the former `safwa.ai.provider` module is gone
- one OpenAI SDK import, inside `llm_gateway.openai_compatible`; ASR uses that adapter's client
  factory rather than importing the SDK directly
- provider contract coverage for tools, raw malformed `arguments_json`, structured output,
  temperature and reasoning dialects, usage/cache fields, empty-response retries, and the
  scripted double
- `docs/LLM_GATEWAY.md`

Verification: `ruff check .` and `pytest -q --basetemp .pytest-phase1` pass. The test-local base
directory is used only because the machine's global `%TEMP%` was full; no user temporary files
were removed. Architecture metrics remain at 75 entity dispatch points, 0 use-case bases, 6 large
modules, 0 reusable-package violations, and 0 import cycles (281 edges).

## What Phase 2 delivered

The seam every business batch after this one is isolated behind. Features are still thin wrappers:
only who lists whom changed, and `domain.py`, `telegram/cards.py` and the rest stayed where they are.

- `src/safwa/bootstrap/` — `module_manifest.py` (the wiring DTOs) and `modules.py`, the one place
  that names the features. See [FEATURE_MODULES.md](FEATURE_MODULES.md).
- `src/safwa/features/{planning,diary,reminders,saved_requests,proposals,continuity}/` — each with
  its own `module.py`, and the `views.py`, `agent.py`, `proposal.py`, `telegram.py`,
  `background.py` it actually needs. `features/proposals/api.py` holds the three contracts.
- `ChangePreparer.prepare` is orchestration plus `ProposalHandler.prepare`; `ProposalService`'s
  `_apply_*` methods are `ProposalHandler.apply`; the review screen and the receipt lines are
  `ProposalPresenter`. `ai/service.py` fell from 3062 lines to 2197.
- `ALLOWED_VIEWS` and `create_ai_views` come from `m.views`. The catalogue is passed as data —
  `ReadOnlyQueryRunner(path, views)`, `validate_read_sql(sql, views)`,
  `normalize_request_sql(raw, views)`, `Services.views` — so `ai/sql.py` stays a leaf and the
  import graph stays acyclic.
- The subagent roster and the `# Routing` section of `SYSTEM_PROMPT` are built from `m.agents`; an
  `AgentSpec.purpose` **is** its prompt line. `ai/board.py` and `ai/diary.py` moved into their
  features and are gone.
- `recover_startup(session, hooks)`, and every background task, come from `MODULES`.
- `scripts/architecture_metrics.py` takes its entity names from the registry, which is what makes
  Rule H self-maintaining.
- `tests/test_feature_modules.py` — the registry's own promises: three responsibilities per entity,
  unique tool names, the allowlist is the catalogue the database gets, and the routing rules name
  every agent.

Verification: `ruff check .` clean, `pytest -q` 508 passed / 3 skipped (439 test functions). Rule I
passed without a snapshot rewrite — the assembled prompt and every tool schema are byte-identical to
the Phase 0 baseline — and so did Rule J.

| | before | after |
|---|---:|---:|
| Entity dispatch points outside `features/` (DoD #1) | 75 | 28 |
| Modules under `src/` | 57 | 92 |
| Largest module | `ai/service.py` 3062 | `ai/service.py` 2197 |
| Modules over 600 lines (DoD #3) | 6 | 6 |
| Import cycles | 0 (281 edges) | 0 (394 edges) |

### The 28 that remain, and who owns them

Every dispatch point in the proposal seam is gone. What is left is one other registry, split across
two layers, plus the tool-input models:

- `telegram/screens.py` (12), `telegram/callbacks.py` (6), `telegram/dialogue.py` (2),
  `telegram/items.py` (1), `telegram/_core.py` (1), `history.py` (1) — which item kinds can be
  cited, opened and edited by hand. That is the Telegram screen registry, and it moves with the
  handlers in Phases 5 and 8.
- `ai/service.py` (2) — `OPENABLE_MODELS`, the AI half of that same registry, and the wording that
  names a Card when a whole batch is Card creations.
- `ai/contracts.py` (3) — `RemoveToolInput` and `OpenInput`. Each `ToolInput` moves to its feature's
  `agent.py` when `ai/contracts.py` is split.

The allowlist gained exactly one key, `Rule H|safwa/ai/service.py|dict over card, check, diary,
request, tag, value`: `prepare.py`'s `ENTITY_MODELS` served two callers, and the `open` half of it
had nowhere better to go this phase. Its old key, which was larger, is gone.

### Known behaviour left as it was

`_refresh_queued_proposal` re-reads the expected version only for Card, Tag, Value and Request. That
is preserved exactly, as `ProposalHandler.version_model = None` on the other three handlers. Whether
a requeued Check, Diary day or Reminder should be re-snapshotted is a product question for the
Phase 6 proposal scenarios, not a technical batch's call.

## What Phase 3 delivered

The first vertical business feature is complete. Its approved behaviour and test audit live in
[brd/diary.md](brd/diary.md): one entry per local date, whole-entry replacement, optional 0–10
feeling score, deletion, Advisor-owned reads and citations, proposal-only AI writes, compact
Save/Discard receipts, and direct opening by date without routing.

- `features/diary/model.py` owns `DiaryEntry`; `use_cases.py` owns the create, read, replace and
  delete operations. Agent and presentation constants stay in their adapters. Proposal application
  and direct UI/test callers use the same operations.
- `features/diary/agent.py` owns `DiaryToolInput`, the Diary mutation tool, its prompt and day reader.
  The day reader and Diary clock are the first production consumers of `foundation/clock.py`, so
  their local-date behaviour is tested with an injected clock rather than the process wall clock.
- `features/diary/telegram.py` owns both proposal presentation and the complete read-only Diary
  screen. The old `telegram/diary.py` path is gone.
- Shared ORM base types, workspace revision operations and domain errors moved to `foundation/` so
  the feature does not depend on the flat `models.py` or `domain.py`. `safwa.models` keeps explicit
  compatibility imports while the remaining features migrate; it also makes the full metadata
  visible to startup. The declared database schema is unchanged.
- The owner selected the current read model: the Advisor queries `ai_diary`, cites entries and may
  call `open(item_type="diary", id=...)` directly. Only create, replace and delete route to the Diary
  subagent.

Phase boundaries were kept intact: Diary Reminder/profile behaviour remains in Phase 4, generic
Save/Discard resumption remains in Phase 7, and the central open/screen registries remain for Phase 8.

The approved Diary scenario contract is [`tests/brd/diary.feature`](../tests/brd/diary.feature).
It is intentionally not run by Behave: the unit and E2E pytest tests trace each `DI-*` scenario
back to that file. `docs/brd/diary.md` was the approval artifact for this migration batch.

Verification: `ruff check .` clean; `pytest -q` 513 passed / 3 skipped; prompt and schema snapshots
unchanged; 0 import cycles (408 edges). The move
introduced no new central dispatch and no use-case base abstraction: DoD #1 remains 28 and DoD #2
remains 0. `domain.py` fell from 1908 to 1838 lines.

## What Phase 4.a delivered

Continuity and Profile are feature-owned. Their approved behaviour and test audit live in
[brd/continuity.md](brd/continuity.md) and [brd/profile_settings.md](brd/profile_settings.md).

- `features/continuity/` owns `memory.py` (the authoritative `memory.md` and its rebuildable
  cache), `service.py` (Summary and AI memory maintenance), `storage.py` and `background.py`.
- `features/profile/` owns `model.py` (`UserProfile`, `ProfileField`), `use_cases.py` (the
  validated write and the Diary trigger it derives), `api.py` and `screens.py`.
- Profile context follows `memory.md` in the assembled prompt; the Diary Settings reconcile only
  the system Reminder that belongs to no Sprint.

### What the Phase 4.a review changed

The batch was reviewed after the fact and the following was corrected in the same phase.

Behaviour that had been dropped without a scenario behind it:

- `memory.md` lost its read-side guards. A file that is not UTF-8 raised out of the background
  poll task, which `asyncio.create_task` then swallowed, so memory stopped syncing for the rest of
  the process; an oversized file was injected into every prompt, because the token budget bounded
  only AI writes. Both are back under CO-MEMORY-012. `MemorySyncState.error` is written again, AI
  maintenance refuses a file it could not read, and `/mem` says so instead of raising.
- `/status` printed a literal `Memory: OK`. It reports the recorded reason again.
- Every background task now carries a done callback: a loop that ends before shutdown is logged
  rather than lost.
- `/mem` and `append_manual` had no test at any level. CO-MEMORY-014 covers the append.

Structure:

- One file vocabulary. `profile/api.py` held the write and `profile/use_cases.py` held a recovery
  hook; the write is now `use_cases.py`, `ProfileField` sits with its entity in `model.py`, and
  `api.py` is what another feature may call — one function, `scheduled_memory_time`, so Continuity
  no longer imports `UserProfile`. `continuity/api.py` was a pure re-export and is gone.
- `estimate_tokens` moved to `foundation/tokens.py`: the dialogue window, the Summary trigger and
  the memory file all spend the same context and were counting it through a feature.
- `SummaryState` is no longer written from `telegram/_messaging.py`; `record_summary` is the
  Continuity operation it calls.
- `BackgroundContext` no longer carries `MemoryFileStore` and `PersonaContinuity`. A field per
  feature in the shared manifest is the registry `MODULES` exists to remove; a task takes its own
  objects off `services`.
- `query_read_tool` and `QUERY_SAFWA_TOOL` moved from `ai/service.py` to `ai/mini.py`, so
  `features/diary` and `features/planning` no longer import the 2146-line module.
- `features/profile/screens.py` and `telegram/{callbacks,commands,dialogue}.py` were an import
  cycle: `tests/test_profile.py` could not be collected on its own and passed only because another
  module imported `safwa.telegram` first. The three telegram modules now import the Settings screen
  inside the handlers that use it, until Phase 8 moves them.

Contracts:

- `Database.transaction()` refuses to open inside itself. See
  [FEATURE_MODULES.md](FEATURE_MODULES.md#who-owns-the-transaction) — this amends plan §5: a use
  case takes the session and the caller owns the transaction, because every write path already
  arrives inside someone else's session.
- `set_profile_field(session, field, value, *, clock)` replaces `update_profile(session, **fields)`.
  The enum is the allowlist, and the UI edits one field at a time anyway.
- The Diary trigger reconciliation takes a `Clock` instead of reading the process clock, which is
  what PS-DIARY-012 needed to be testable.

Verification: `ruff check .` clean; `pytest -q` 537 passed / 3 skipped; prompt and schema snapshots
unchanged; 0 import cycles (445 edges).

| | Phase 3 | Phase 4.a |
|---|---:|---:|
| Entity dispatch points outside `features/` (DoD #1) | 28 | 28 |
| Use case base abstractions (DoD #2) | 0 | 0 |
| Modules over 600 lines (DoD #3) | 6 | 5 |
| Re-export-only modules (DoD #13) | 0 | 0 |
| Largest module | `ai/service.py` 2197 | `ai/service.py` 2146 |
| Largest Continuity module | `service.py` 340 | `persona.py` 244 |
| `domain.py` | 1838 | 1755 |

### The second review pass

- `continuity/service.py` mixed four roles and is gone. `agent.py` holds the three provider
  prompts, `persona.py` the long-lived `PersonaContinuity`, `use_cases.py` the three operations,
  `model.py` the rows (renamed from `storage.py`). The largest is now 244 lines.
- Every feature uses the same file names: `model.py`, `use_cases.py`, `api.py`, `agent.py`,
  `telegram.py`, `proposal.py`, `views.py`, `background.py`, `module.py`. A public class large
  enough for its own module is named after it (`memory.py`, `persona.py`) and is not a new role.
- `features/reminders/api.py` now owns `sync_daily_system_reminder`: Profile says what the Diary
  trigger should say and when, and Reminders keeps the row, its schedule and its arithmetic. That
  is the Rule E door the Phase 4 ordering had skipped, and Profile's `use_cases.py` fell from 160
  lines to 96.
- Rule C was flagging `SummaryState` and `MemorySyncState` once they moved into `model.py` — a
  name-suffix heuristic hitting persisted rows. The rule now skips ORM entities: a row is durable
  state, mutable by definition, not the frozen process-state union the rule is about. That is a
  narrowed rule, not a lowered allowlist.

### Traceability is checked now

`tests/test_brd_traceability.py` reads the `.feature` files and every test docstring and fails on
an approved scenario with no test, a citation naming no scenario, a docstring in any other shape,
an undeclared prefix, or a repeated scenario title. It found the drift the moment it was written:
eight Continuity and two Settings tests still carried the old docstring form.

The identifier stays written once, in the docstring. `tests/conftest.py` reads it from there and
attaches the marker, so `pytest -m brd` and `pytest --brd=DI-DAY-001` need nothing kept in step.
`docs/brd/README.md` had `CT` for Continuity where every scenario says `CO`, and no row for `PS`
at all; both are fixed, and the table is what the test reads.

The `.feature` files are one format now — `Background` and the em dash after the identifier —
because Diary had one shape and the Phase 4.a files had another.

### Scenarios the review found missing

Diary gained `DI-DAY-011` (a day with nothing written is never saved), `DI-DATE-012` (today is
the owner's local day, which past midnight UTC is a different date), `DI-READ-013` (the subagent
holds both readers, because button work never reaches the conversation and how a day felt never
reaches the database) and `DI-READ-015` (a day nobody talked about reads as empty rather than
failing). All four document behaviour the code and the product spec already agreed on and nothing
covered.

`DI-MOOD-014` was the one open question and the owner settled it on 2026-08-22: **an omitted
`feeling_score` keeps the saved one**, and only a score the owner asks for changes it. Omitting the
score is a statement about that day's evidence, not a request to erase a mood the owner already
gave. `pov` is still replaced whole.

The rule lives in `DiaryProposalHandler._resolve_day`, which has already loaded the saved day, so
the review screen and the receipt show the score Save will store. `update_diary_entry` stays a
plain whole replacement and never has to tell an omitted score from a deliberate one — no sentinel,
no second meaning for `None`. `DIARY_PROMPT` and the `feeling_score` field description say it in
one line each; `DIARY_PROMPT` and `tool:diary` are the only two prompt-prefix hashes that moved,
and `SYSTEM_PROMPT` is untouched. There is deliberately no signal that clears a score back to none:
nothing asked for one.

The Summary window was **not** given a scenario. The dialogue a Summary covers does not leave the
window; it stays as a bounded `summary_context` tail. That is adapter behaviour, `tests/test_history.py`
covers it, and `docs/brd/continuity.md` already assigns it there.

### The third review pass

The owner read the scenarios back and found the same fault in three places: two Scenario blocks
under one identifier, saying two different things.

- `DI-READ-013` was which readers the subagent holds *and* how an empty read behaves. The second
  is now `DI-READ-015`.
- `PS-DIARY-012` was the schedule arithmetic *and* the startup hook that runs it. The second is
  now `PS-DIARY-013`.
- `CO-MEMORY-012`, `CO-MEMORY-013` and the second `CO-MEMORY-014` block were one rule stated three
  times, once per door. They are one `CO-MEMORY-012`; `CO-MEMORY-013` is retired and not reused.
  An unusable resource is not normally worth a scenario — Safwa writes none for an unreachable
  database — and this one is only because the answer is not the standard one: it is the owner's
  own file, the hash guard that protects it everywhere else passes on a file that could not be
  decoded, and the failure is silent.
- `DI-DAY-011` had one rule and two tests, the feature operation and the tool contract. The
  contract refusal is the same rule at an earlier door and `DiaryToolInput` is already covered by
  DI-MOOD-004's validation test, so the duplicate test is gone.

Two blocks under one identifier stay only when they are two observable cases of the same question
— `DI-DELETE-005` and `DI-OPEN-010` are a rule's two branches, and `DI-DATE-012` is one rule at
two doors. `docs/brd/README.md` says so now.

The `@tag` lines are gone from every `.feature` file. There is no BDD runner, nothing read them,
and a `@di_day_011` above `Scenario: DI-DAY-011 — …` is a lowercase second copy of the identifier
with nothing keeping it in step. `test_brd_traceability.py` fails on a tag, so they cannot return.

Two of the deferred items are also closed:

- `PersonaContinuity` no longer holds a lock. `GenerationGuard.run_background` is the single lease
  every production caller takes and it already refuses a second background run; the locks were a
  second mechanism for the property CO-GENERATION-011 assigns to the guard.
- `MemorySyncState.warning_sent_at` is removed. It had no writer, and the schema snapshot is
  regenerated for it — the only hash that changed is `memory_sync_state`.

Verification: `ruff check .` clean; `pytest -q` 540 passed / 3 skipped; 0 import cycles
(445 edges); Rules A–F and K at 0, G at 2 and H at 28 as before; DoD #1 28, #2 0, #3 5, #13 0.
Both snapshots were regenerated for declared changes and nothing else moved in them:
`memory_sync_state` for the dropped column, `DIARY_PROMPT` and `tool:diary` for DI-MOOD-014.

### Known and deferred

- The Settings screen still registers on the shared `telegram` router and is dispatched from
  `CALLBACK_ACTIONS`, and three telegram modules import it inside their handlers to break the
  cycle. Moving it needs `FeatureModule` to carry commands, callback actions and text-input flows
  — a mechanism Reminders, Values, Tags and Cards all need too. Building it for Profile alone is
  the per-feature field `module_manifest.py` exists to refuse. It moves with the Phase 8 handler
  batch, whole.
- `features/reminders/api.py` reached the flat `reminders.py` and `models.Reminder`. Closed by
  4.b.1: both are inside the feature.

## Before starting the rest of Phase 4

Read this section first. It is what 4.a cost to learn, written so the next batch does not pay
again.

### What is left, and in what order

The plan's order is Reminders, Saved Requests, Values and Tags, Profile, Continuity. 4.a took
Profile and Continuity out of turn because the review of Phase 3 landed on them. What remains:

| Batch | Moves | Already there |
|---|---|---|
| ~~4.b Reminders~~ | **done** — 4.b.1 the record, 4.b.2 the firing | — |
| ~~4.c Saved Requests~~ | **done** | — |
| 4.d Values and Tags | the Value and Tag half of `domain.py` | nothing; the feature package does not exist |

Each needs its own approved scenario package before its tests are written. None of them creates a
Manager: none has a long-lived process.

### Do not move the screens

Every one of these features has its Telegram screen in the shared package —
`telegram/reminders.py`, `telegram/items.py` for Values and Tags. Leave them there.

Moving a screen into its feature is what made `features/profile/screens.py` an import cycle: the
screen needs `telegram/_core.py` and `telegram/_messaging.py`, and the shared handlers need the
screen back. 4.a paid for that with three deferred imports inside handler bodies. Doing it again
per feature adds another three each time.

The real fix is `FeatureModule` carrying commands, callback actions and text-input flows, and it
belongs to the Phase 8 handler batch, done once for everyone. A per-feature field in
`module_manifest.py` is exactly what that file's docstring refuses. If a batch feels blocked
without it, that is the signal to stop and say so — not to build a one-user mechanism.

### The five things 4.a got wrong

Each one shipped green and was found by reading, not by a failing test.

1. **A guard was deleted with no scenario behind it.** `memory.md` lost both read-side guards in a
   file move. Before deleting a check, find the scenario that owns it; if there is none, that is a
   missing scenario, not permission.
2. **A background loop swallowed its own death.** The exception left the loop, `asyncio.create_task`
   dropped it, and memory silently stopped syncing for the life of the process. Every loop now
   needs a per-iteration `try/except` with `logger.exception`, and every task a done callback.
   Check both for any loop a batch touches.
3. **`/status` printed a hardcoded `Memory: OK`.** A status line that cannot say "broken" is worse
   than no status line.
4. **A use case opened its own transaction.** A use case takes an `AsyncSession` and never commits;
   the caller owns the transaction and `Database.transaction()` refuses to nest. See
   [FEATURE_MODULES.md](FEATURE_MODULES.md#who-owns-the-transaction).
5. **`api.py` held the feature's own write.** `api.py` is only what *another* feature calls, and it
   hands over the answer rather than the row. Everything the feature's own adapters call is
   `use_cases.py`.

### Writing the scenario package

The third review pass rewrote a third of 4.a's scenarios. These are the rules it produced.

- **One identifier is one rule.** Two `Scenario:` blocks may share an identifier only when they are
  two branches of the *same* question. Two different rules under one identifier hide the second,
  and nothing fails to say so.
- **A standard failure is not a scenario.** Safwa writes none for an unreachable database. Write
  one when the answer is *not* the standard one, and say in the packet which part is not obvious —
  CO-MEMORY-012 exists because the hash guard that protects the file everywhere else passes on a
  file that could not be decoded.
- **One rule, one test.** A second test for the same rule one layer down is a duplicate, however
  different the code path looks. DI-DAY-011 had the feature operation and the tool contract; the
  contract half is gone.
- **No Gherkin tags.** `test_brd_traceability.py` fails on one. The identifier lives on the
  `Scenario:` line and in the test docstring, nowhere else.
- **A product decision ships as `question`, not as a test.** DI-MOOD-014 sat open for a review
  cycle rather than being settled by whichever test got written first. That was right; do it again.

### Snapshots: which hashes may move

Regenerating a snapshot is routine only when the batch declared the change. Read the diff:

- `memory_sync_state`, `reminders`, any one table in `schema.json` — expected when that batch owns
  the model.
- `DIARY_PROMPT`, `tool:diary`, `tool:reminder` — expected when the batch changes that contract.
- **`SYSTEM_PROMPT` or `PERSONA` moving is a red flag.** That is the Advisor's cache prefix. If a
  batch moves it without meaning to, something volatile got into `messages[0]`; find it rather than
  accepting the new hash.

## What Phase 4.b.1 delivered

The Reminder record is feature-owned. Its approved behaviour, the three decisions the owner made
with it, and the test audit live in [brd/reminders.md](brd/reminders.md).

Reminders was too big for one packet — 24 scenarios against a five-to-fifteen guideline — so it is
two batches with one approval behind them. 4.b.1 is what a Reminder *is* and how one is written;
4.b.2 is what happens when one comes due. The scenarios enter `tests/brd/reminders.feature` as
each batch writes its tests, because an approved scenario with no test fails traceability.

- `features/reminders/schedule.py` owns `Schedule` and the arithmetic — resolution, `next_fire`,
  `roll_forward`, quiet windows, the column and payload round trips, `describe()`. It is named
  after its public class, like `memory.py` and `persona.py`.
- `features/reminders/model.py` owns the `Reminder` row; `safwa.models` keeps the compatibility
  import so startup still sees the whole metadata. The declared schema is unchanged.
- `features/reminders/use_cases.py` owns create, edit-text, reschedule and delete. `domain.py`
  fell from 1755 to 1693 lines and now calls the feature for the Sprint's end warnings.
- `features/reminders/agent.py` owns the whole model contract: the mutation tool and the setup
  session that resolves free-text timing. `ai/reminder_sessions.py` is gone.
- `parse_clock_or_off` moved from the schedule module to `api.py`. Profile's Settings screen is
  the only caller, and reaching past `api.py` is the Rule E violation the architecture test caught
  the moment the module moved into the feature.

### The gap the approved scenarios found

RM-WRITE-010 says the model may propose removing a Reminder. It could not. `remove` may only send
`mode="archive"` for anything that is not a Card, and `ReminderProposalHandler.apply` knew
`create`, `update` and `delete` — so Save raised and the row survived. A Reminder has no archive,
so the handler now reads `archive` as the removal it is, and the receipt says `Delete` rather than
`Archive`. Found by writing the test for an approved rule, not by any existing test failing.

Verification: `ruff check .` clean; `pytest -q` 543 passed / 3 skipped; both snapshots unchanged,
as the batch declared; 0 import cycles (457 edges); Rules A–F and K at 0, G at 2, H at 28.

| | Phase 4.a | Phase 4.b.1 |
|---|---:|---:|
| Entity dispatch points outside `features/` (DoD #1) | 28 | 28 |
| Use case base abstractions (DoD #2) | 0 | 0 |
| Modules over 600 lines (DoD #3) | 5 | 5 |
| Re-export-only modules (DoD #13) | 0 | 0 |
| `domain.py` | 1755 | 1693 |

## What Phase 4.b.2 delivered

Reminders is whole. The poll, the escalation, startup reconciliation and both surfaces are
feature-owned, and `scheduler.py` is gone.

- `features/reminders/background.py` is the poll engine: `Firing`, `due_reminders`, `is_stale`,
  `prepare`, `settle`, `tick`, `run_scheduler`. It takes its gate and its escalation as callables
  and imports no adapter.
- `features/reminders/telegram.py` holds both ways a Reminder reaches the owner: the proposal
  presenter and `ReminderRuntime` / `format_escalation`. `telegram/escalation.py` is gone, and the
  `telegram` package no longer exports either name.
- `features/reminders/use_cases.py` gained `reconcile_reminders` from `recovery.py`.
- `run_sprint_expiry` moved to `features/planning/background.py`, its only caller. `scheduler.py`
  is deleted.

### The cycle, and what it was telling us

Moving the escalation next to the poll made `background.py` and `telegram.py` import each other —
the engine wanted `ReminderRuntime`, the adapter wanted `Firing`. A `TYPE_CHECKING` import hides
that from the interpreter but not from Rule A, and rightly: the fault was that one module held both
the engine and the wiring that binds it to an adapter. `_poll_due_reminders` moved to `module.py`,
which is the file whose whole job is what this feature plugs into the application. The edge now
runs one way, `telegram.py` → `background.py`, and no deferred import was needed anywhere.

### The two behaviour changes, both decided in the packet

- **A Sprint's end warnings are `system` (Q2).** They were ordinary Reminders, so the owner saw
  triggers they never set and could edit or delete them, and the model read them in `ai_reminders`.
  The test was written first and failed; the fix is one line where `sprint_id` was already set.
  `finish_sprint` deletes by `sprint_id` without going through the refusing path, and
  `sync_daily_system_reminder` already selected only the system Reminder belonging to no Sprint.
- **`ai_reminders` exposes `next_fire_at_local` (Q3).** The schedule columns stay raw — `describe()`
  is the one wording and a second one in SQL would be free to disagree with it — but the next fire
  is what the model quotes back to the owner, so it reads in the owner's clock. SQLite has no
  timezone database, so `ai/sql.py` registers a `local_time()` function on the read-only connection
  and `ReadOnlyQueryRunner` takes the timezone. A fixed offset in the view SQL would be an hour
  wrong for half of every daylight-saving year. `CREATE VIEW` does not resolve the function, so only
  the read path needs it.

Six scenarios had no test at all and now do: RM-FIRE-014, RM-GATE-017, the refusal half of
RM-SYSTEM-022, the `/reminders` screens in RM-UI-023, the frequent-interval case Q1 settled, and
the prompt-prefix half of RM-READ-024.

Verification: `ruff check .` clean; `pytest -q` 550 passed / 3 skipped; 0 import cycles (453 edges);
Rules A–F and K at 0, G at 2, H at 28. `schema.json` unchanged. `prompt_prefix.json` moved on
exactly the two hashes the batch declared, `SYSTEM_PROMPT` and `BOARD_PROMPT`, and only because the
`ai_reminders` column list is prose in both; `PERSONA`, `DIARY_PROMPT` and `tool:reminder` are
untouched.

| | Phase 4.b.1 | Phase 4.b.2 |
|---|---:|---:|
| Entity dispatch points outside `features/` (DoD #1) | 28 | 28 |
| Use case base abstractions (DoD #2) | 0 | 0 |
| Modules over 600 lines (DoD #3) | 5 | 5 |
| Re-export-only modules (DoD #13) | 0 | 0 |
| Modules under `src/` | 108 | 106 |
| Import cycles | 0 (457 edges) | 0 (453 edges) |

## What Phase 4.c delivered

Saved Requests is feature-owned. Its approved behaviour, the three decisions the owner made with it,
and the test audit live in [brd/saved_requests.md](brd/saved_requests.md).

- `features/saved_requests/model.py` owns `SavedRequest`; `safwa.models` keeps the compatibility
  import so startup still sees the whole metadata. Not one column type changed.
- `features/saved_requests/use_cases.py` owns create, update, archive and `request_cards`.
  `domain.py` fell from 1693 to 1608 lines and no longer knows Requests exist.
- `normalize_request_sql` and `RequestQueryError` moved to `ai/sql.py`, next to `validate_read_sql`.
  Two callers, and neither owns it: a Request's SQL, and the `parent_query` a Card proposal resolves
  its parent with. The rule it encodes — a read-only SELECT that comes back with Card ids — is a
  property of the read surface, and putting it there keeps Planning from importing the Requests
  feature for something that is not a Request.
- `src/safwa/saved_requests.py` is gone. The `/requests` screens, the Request detail and the Plan
  filters stay in the shared `telegram` package, as the Phase 4 rule says, and Phase 8 moves them.

### The three decisions

- **Q1 — a Request runs behind validation, and the docs now say so.** `CLAUDE.md` and
  `ARCHITECTURE.md` both claimed saved Requests sat behind the full triple guard; `request_cards`
  runs on the ordinary session, so the regex validator is the whole guard. The first recommendation
  was to route it through `ReadOnlyQueryRunner` and it was **withdrawn after reading its numbers**:
  `DEFAULT_ROW_LIMIT = 50` would have silently capped a Request over a 300-Card Backlog, and the
  Plan filters intersect result sets, so a capped set is a wrong Backlog with no error anywhere. The
  owner settled it with the reason that makes it obvious — **a Request's result is always a list in
  the interface and never enters the model's history**, and every cap in that runner exists because a
  local model pays for what it reads. So the query has to be valid and nothing else. The two
  documents are corrected instead.
- **Q2 — the ids stay "mentions `ai_cards` and returns `id`".** Requiring `ai_cards.id` would break
  legitimate CTE and UNION queries to close a rare wrong answer the owner sees on the review screen.
- **Q3 — `normalize_request_sql` lives in `ai/sql.py`.** See above.

### Two scenarios were withdrawn during review, and both were right to withdraw

- **The Plan filter rule.** Drafted as a Request scenario, it turned out to be three Plan-screen
  rules and one Request rule already stated elsewhere. It belongs to the Planning packet in Phase 5.
- **A proposal applying against state that moved.** It has no reachable trigger for a Request. A
  screen is never something the owner comes back to — a UI message is only ever the last message in
  the chat and never moves back up, so anything done below a proposal interrupts it and
  `cancel_approval_for_target` sets every pending proposal in that batch to `REJECTED`. Inside one
  batch `_refresh_queued_proposal` re-snapshots the expected version and the workspace revision
  together; a Reminder cannot escalate over a pending proposal at all; and `ProposalService.apply`
  checks `workspace.revision` before any handler runs, while every Request write bumps it. The
  per-entity version check would need a writer that moves a Request's version without moving the
  workspace revision, and there is none. The generic rule is Phase 6's.

`CLAUDE.md` said "the screen freezes, the session is saved", which reads as a live screen waiting to
be pressed. Only the subagent's session survives. That sentence is corrected in this batch — it is
what produced the withdrawn scenario.

Eight scenarios had no test at all and now do: the absence of a hand-written path, the update-side
name refusal and the empty name, re-validation at run time, a duplicated id returned once, unsafe
SQL becoming a tool error rather than a proposal, the archive taking a Request off every surface, a
SQL change never reaching the autoapproval reviewer, and the `/requests` list with its result cap and
its way back.

Verification: `ruff check .` clean; `pytest -q` 560 passed / 3 skipped; **both snapshots
byte-identical and not regenerated**, as the batch declared; 0 import cycles (458 edges); Rules A–F
and K at 0, G at 2, H at 28.

| | Phase 4.b.2 | Phase 4.c |
|---|---:|---:|
| Entity dispatch points outside `features/` (DoD #1) | 28 | 28 |
| Use case base abstractions (DoD #2) | 0 | 0 |
| Modules over 600 lines (DoD #3) | 5 | 5 |
| Re-export-only modules (DoD #13) | 0 | 0 |
| Modules under `src/` | 106 | 107 |
| `domain.py` | 1693 | 1608 |

### Done means

`pytest -q`, `ruff check .`, and `scripts/architecture_metrics.py` — plus: allowlist counts and DoD
numbers may only fall, import cycles stay 0, and any snapshot line that moved is one the batch
declared. 4.a ended at 540 passed / 3 skipped, DoD #1 28, #2 0, #3 5, #13 0, 445 edges, 0 cycles.
