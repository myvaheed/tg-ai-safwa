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
| 4 | Leaf business batches | business | **done** — 4.a Continuity and Profile, 4.b Reminders, 4.c Saved Requests, 4.d Values and Tags |
| 5 | Cards, Checks and the Sprint | business | **done** — 5.0 the board package (technical), 5.a Cards, 5.b stages, 5.c Checks, 5.d archiving, 5.e the archive as a mark, 5.f the heavy analyzer, 5.g a repeat's Values, 5.h Planning and the plan screen |
| 6 | Proposals and the first reactive process | business | not started |
| 7 | `agent_runtime` | technical + business | not started |
| 8 | `telegram_llm` and `TurnManager` | technical | not started |
| 9 | Packages and cleanup | technical | not started |

A phase ends in a state that can be kept forever: tests green, bot working, no old path running
beside a new one. A phase that cannot be finished is rolled back whole.

## What can be deleted, and what can only be archived

**Everything is deleted. Only a Card and a Check are also archived**, because those are the two the
owner makes many of and that reach a state where they are over. Archiving exists so the workspace
does not fill up: it hides a closed Card or Check two Sprints after it closed
(`ARCHIVE_AFTER_SPRINTS`), and an archived one still counts in effort, in completed Cards and in
every trend that counted it.

- Archiving by hand is refused for anything but a **Card or a Check**, and refused for one that is
  not closed yet. `remove(mode="delete")` is the default and takes every entity.
- A **Value**, a **Tag** and a **Saved Request** carry no `archived_at` at all. Deleting one breaks
  its links and leaves what carried it standing.
- Deleting a Card deletes its Checks, except one that carries a Value: that Check is also a
  measurement, so it stays, on no Card.

The rule is `docs/brd/archive_and_delete.md`, approved 2026-08-24.

## Completion feedback was removed in Phase 5, and will be built again

The owner ruled on 2026-08-24 that the completion feedback loop — the thumbs-up asked after an Action
is Done — is torn out rather than specified, and designed again from scratch afterwards. No packet
writes a scenario for it, and no scenario may mention it until the one that rebuilds it.

It went with 5.b: `FeedbackQueue`, `Card.liked`, `set_feedback`, `move_card`'s two reopen lines that
cleared them, the `/feedback` command and `render_feedback`, the `feedback` callback, the pending
count on `/status`, and the `liked` inputs to `analytics.py` including the capacity advice.

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
| ~~4.d Values and Tags~~ | **done** — and it grew a feature, see below | — |

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

Reminders is whole. The poll, the delivery, startup reconciliation and both surfaces are
feature-owned, and `scheduler.py` is gone. (The delivery half moved out again to `cues/` — see
[What the Cue batch delivered](#what-the-cue-batch-delivered).)

- `features/reminders/background.py` is the poll engine: `Firing`, `due_reminders`, `is_stale`,
  `prepare`, `settle`, `tick`, `run_scheduler`. It takes its gate and its voice as callables and
  imports no adapter.
- `features/reminders/telegram.py` holds both ways a Reminder reaches the owner: the proposal
  presenter and the turn that speaks. `telegram/escalation.py` is gone, and the `telegram` package
  no longer exports either name.
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

## What Phase 4.d delivered

Values and Tags are feature-owned, and a Check can carry a Value. Their approved behaviour, the five
decisions the owner made with them, and the test audit live in [brd/values_tags.md](brd/values_tags.md);
the scenarios are [`tests/brd/values.feature`](../tests/brd/values.feature) and
[`tests/brd/tags.feature`](../tests/brd/tags.feature) — two files, because a Value is a focus and a
Tag is a label for finding Cards, and those are two different businesses.

- `features/planning/model.py` owns `Value`, `Tag`, `CardValue`, `CardTag` and the new `CheckValue`;
  `safwa.models` keeps the compatibility imports. Not one existing column changed.
- `features/planning/use_cases.py` owns the seven Value and Tag writes. `domain.py` fell from 1608 to
  1516 lines.
- `domain.effective_value_ids` is **deleted**. It had no production caller, no screen, no view and no
  place in the spec; two assertions were holding it up.

### The Check link, which the owner asked for inside this batch

A Card is work that *serves* a Value; a Check shows *how well the Value is actually held to*. So a
Check now carries Values of its own, written from the Check side the way a Card writes its own links.
Nothing is derived between a Check's Values and the Values of the Cards it belongs to — they are two
different statements, and VL-CHECK-010 says so out loud.

- `ReferenceSpec` gained `owner_key`, `owner_label` and `also_carried_by`. The first two make the
  linking side a parameter instead of always a Card; the third is how the Value screen counts Checks
  **without branching on the entity name** — the first attempt did branch, and Rule H caught it.
- `archive_value` clears `check_values` in the same transaction, `delete_subtree` clears it for the
  Checks it deletes, and an answered repeatable Check hands its Values to its successor. That last one
  is the owner's: without it a Value would gain one finished Check per repeat cycle for ever. The same
  question is open for a repeatable Card and is Phase 5's.
- The `check` tool gained `link` and `unlink`, `ai_checks` gained `direct_values`, the Check screen
  gained a Values picker, and the Check review screen shows them.

### The five decisions

- **Q1 — autoapproval may still flip a Value's focus.** Unchanged, but the rule left this packet: it
  came up in three batches running, so the whole allowlist goes to the Proposals and autoapproval
  packet and is written once.
- **Q2 — `effective_value_ids` is deleted.** See above.
- **Q3 — the create screen no longer wipes a description it never showed.** Safwa's door sent nothing
  and the stored description survived; the owner's door sent an empty box, which overwrote. Reviving
  an archived Value by name erased its description with nothing on screen that looked like a deletion.
- **Q4 — ticking a Tag on page 2 stays on page 2.** The selector reopened without its page, so paging
  undid itself on every tap. The fix is in the shared handler, so Checks, Categories and Energy types
  got it too.
- **Q5 — a Check can carry a Value, now rather than in Phase 5.** The concern that this turns a file
  move into a feature was stated, the owner reaffirmed, and this section is the record of what it cost.

### Two things the review should know

- **Implementation ran ahead of the tests for Q5.** The packet said the five new rules would be
  written test-first and fail first; they were not — the code landed first and the tests after. Each
  one was then verified by reverting its production line and confirming the test fails, and all five
  do. That check is the only reason the claim is worth anything, and it is not a substitute for the
  order the process asks for.
- **DoD #3 rose from 5 to 6.** `features/planning/telegram.py` is 616 lines, up from 584, because the
  Check review screen has to render Values. It is the only number that rose. Phase 5 owns that file
  and is where it should be split; splitting it here for a line count would have been the wrong reason.

### Scenarios that found something

`test_manual_check_screens_only_repeat_and_answer` and the archive receipt test both had to learn
about the new surface, which is the point of having them. Nothing else in the suite moved.

Verification: `ruff check .` clean; `pytest -q` 582 passed / 3 skipped; 0 import cycles (465 edges);
Rules A–F and K at 0, G at 2, H at 28. Snapshots moved on exactly the four hashes the batch declared
— `schema.json` gained `check_values` and no existing table moved, `prompt_prefix.json` moved
`SYSTEM_PROMPT`, `BOARD_PROMPT` and `tool:check`, and `PERSONA`, `DIARY_PROMPT` and every other tool
are untouched.

| | Phase 4.c | Phase 4.d |
|---|---:|---:|
| Entity dispatch points outside `features/` (DoD #1) | 28 | 28 |
| Use case base abstractions (DoD #2) | 0 | 0 |
| Modules over 600 lines (DoD #3) | 5 | **6** |
| Re-export-only modules (DoD #13) | 0 | 0 |
| Modules under `src/` | 107 | 109 |
| Import cycles | 0 (458 edges) | 0 (465 edges) |
| `domain.py` | 1608 | 1516 |

## After 4.d: one package per `.feature`, and two words that were one

A technical batch, run the same day 4.d landed. No test changed what it expects, and **every
snapshot came out byte-identical** — the same `SYSTEM_PROMPT`, `BOARD_PROMPT`, every tool schema and
every table hash — which is the evidence that nothing but structure moved.

The owner read the docs back and found that **"planning" meant four different things**: the
workspace mode without a Sprint, the screen where the next Sprint is planned, the block of state the
model is handed, and the package holding Cards, Checks, Values, Tags and the Sprint. The fourth is
the widest of the four and contained the first two, which is backwards.

**The vocabulary now.** The *board* is what the owner keeps — Cards, Checks, Values, Tags, Requests
and Reminders — and it is not a package: it is the set, and `board` is the subagent that proposes
every change to it. *Planning* is the workspace mode without a Sprint, the Sprint, and the screen
where the next one is planned. See [CLAUDE.md](../CLAUDE.md#board-and-planning-are-not-the-same-word).

**The split.** `features/planning` held five entities and is now five packages, one per `.feature`
file: `features/cards`, `features/checks`, `features/values`, `features/tags`, and a `planning` that
is only the Sprint views and the Sprint-expiry task.

- The shared proposal machinery moved to `features/proposals/api.py`, where the rest of it already
  was: `REFERENCE_HINT`, `live_instance_hint`, `reject_closed_repeat`, `validate_named_references`,
  `named_ids`, `reference_names`, `reference_groups` and `NamedItemPresenter`. They are generic over
  `ReferenceSpec` and belong to preparing and presenting a proposal, not to Cards. That is why
  **Rule E stayed at 0**: no feature had to reach into another, and no new `api.py` door was needed.
- `BOARD_AGENT` and `BOARD_PROMPT` live in `features/cards/agent.py`, because Cards are the board's
  centre and the roster already lets a subagent declare mutation tools other features publish. If
  that reads wrong later, it is one file move. It did, and it was: Phase 5.0.
- `MARKER_FORMAT` moved to `ai/sql.py`: two views render the closed-repeat marker, so the wording
  stays in one place.
- `AgentSpec.planning_state` is `board_state`, and `planning_context` is `board_context`. **Two
  strings the model reads still say "planning state"** — one in `SYSTEM_PROMPT`, one on the per-turn
  block. Changing them moves the `SYSTEM_PROMPT` hash, so they wait for a batch that declares it.
- `features/board/` and `features/core/` were empty untracked directories left over from an earlier
  attempt. Deleted. (Phase 5.0 reversed this: `board` is a package, and it holds the subagent.)

**DoD #3 is back to 5.** `features/planning/telegram.py` was 616 lines after 4.d, the one number that
had risen; splitting it by entity is what that number was asking for.

| | Phase 4.d | After the split |
|---|---:|---:|
| Tests | 582 passed / 3 skipped | 582 passed / 3 skipped |
| Entity dispatch points outside `features/` (DoD #1) | 28 | 28 |
| Modules over 600 lines (DoD #3) | **6** | 5 |
| Import cycles | 0 (465 edges) | 0 |
| Modules under `src/` | 109 | 132 |
| Largest feature module | `planning/telegram.py` 616 | `proposals/api.py` 570 |

`docs/brd/values_tags.md` keeps its scenarios and carries a note that its code plan named the old
paths. A packet records the decision that was made; it is not rewritten when a later one moves the
files.

## Phase 5.0: the board is a package

A technical batch, opening Phase 5. The owner reversed the decision recorded above: `board` is a
feature package after all — everything that belongs to the board *subagent*, and nothing else.

- `features/board/` owns `BOARD_AGENT`, `BOARD_PROMPT`, `BOARD_TOOLS` and its read tools.
  `features/cards/agent.py` is the `card` mutation tool and its repair, which is what its docstring
  now says.
- `BOARD` is first in `MODULES`, where `CARDS` was, so the roster order and therefore the `# Routing`
  section of `SYSTEM_PROMPT` are unchanged.
- `tests/test_board.py` is the subagent's own contract: every mutation tool the application
  publishes reaches the board or the Diary and none reaches the Advisor; the board asks for the
  board state it is told to judge against; and the view list its prompt carries names no view the
  database does not have — that list is prose, and a renamed view would fail only inside a query the
  model writes at runtime.

### One prefix per package, and a guard with no door

Two more things the owner asked for while Phase 5.a was being read, both landing here because
neither changes behaviour.

- **`PL` covered five packages, so the scenario prefix now names one.** Cards are `CD`, Checks `CH`,
  Values `VL`, Tags `TA`, and `PL` keeps what `features/planning` actually is — the Sprint and the
  mode without one. The topic went with it: `PL-VALUE-004` said "value" twice and nothing about the
  rule, and it is `VL-LINK-004` now, the way `DI-MOOD-004` and `DI-DELETE-005` have always read.
  The fourteen Value scenarios keep their numbers; the seven Tag ones restart at `001`, because a
  package's numbering begins at its own first scenario. `docs/brd/README.md` carries the new table
  and the rule for the topic, which is what `tests/test_brd_traceability.py` reads.
- **The cycle guard in `validate_parent` is deleted.** It walked the ancestors to refuse a loop, and
  no caller could reach it: a Goal never takes a parent, an Idea only takes a Goal, an Action takes
  a Goal or an Idea and has no children. The owner's rule — do not validate what cannot exist by
  definition. The `card_id` parameter existed only for that walk and is gone with it.

`board_context` stays in `ai/context.py`. It is the board's state, but the Advisor reads it too, and
moving it would mean reaching past four features' models; the Cards and Sprint packets decide where
it lands.

Verification: `ruff check .` clean; `pytest -q` 585 passed / 3 skipped (582 plus the three board
tests); **both snapshots byte-identical and not regenerated**; allowlist unchanged, Rule G 2, Rule H
28; DoD #1 28, #2 0, #3 5, #13 0.

## What Phase 5.a delivered

Cards are feature-owned: what a Card is, where it may sit, and which fields its kind may carry. The
approved behaviour, the three decisions the owner made with it and the test audit live in
[brd/cards.md](brd/cards.md); the scenarios are [`tests/brd/cards.feature`](../tests/brd/cards.feature),
`CD-KIND-001` … `CD-LINK-012`.

### The behaviour change: Blocked is an Action field

A Goal and an Idea are never blocked in their own right. Blocked joins effort, repeat, categories
and energy types, and is stripped for the other two kinds at every door — `create_card`, the Card
proposal, the creation screen and the Card screen — while the domain update door refuses it, the
same backstop the other four have. The two tests were written first and failed first.

**`SYSTEM_PROMPT` moved, and only it.** The Advisor's prompt carries the Action-only list, so Blocked
had to join that line; `PERSONA`, `BOARD_PROMPT`, `DIARY_PROMPT`, every tool schema and every table
in `schema.json` are byte-identical. `BOARD_PROMPT` needed no edit — it never stated the rule.

What a Goal or an Idea shows instead is derived from the Actions in its branch. That derivation is
**not** in this batch: it is parent state derived from descendants, like `effective_stage`, and it
belongs to the packet that owns propagation. The owner's decision is recorded as its source.

### The move, and what it cost

- `features/cards/model.py` owns `Card`, `CardCheck`, `CardCategory`, `CardEnergyType`, `CardEvent`
  and `new_correlation_id`. Not one column changed. `Card.values` names `CardValue` as a quoted
  forward reference and does not import it: the link row belongs to Values, SQLAlchemy resolves the
  target from the registry, and the Card half of a shared aggregate costs no dependency.
- `features/cards/use_cases.py` owns `create_card`, `edit_card_text`, `update_card_fields`,
  `set_card_parent`, the three validators, `card_snapshot`, `record_card_event`,
  `aggregate_child_stages`, `propagate_ancestors` and `card_children`. `domain.py` fell from 1516 to
  1172 lines and imports them back, the way it already did for Values and Tags.
- `features/values/api.py` and `features/tags/api.py` are new doors, and they are why **Rule E stayed
  at 0**: `create_card` used to load a `Value` row and add a `CardValue` row itself. It now asks
  `unlinkable_value_id` and calls `attach_values`, which hand back an answer and take the write.
- `foundation/clock.py` gained `utcnow`, and `domain.utcnow` is that function re-exported. Two
  features and the moved code all wanted the same system clock, and the one they were importing was
  `domain`'s — which the move would have turned into a cycle.
- `EFFORT_POINTS` left `constants.py` for `features/cards/use_cases.py`. `constants.py` is
  cross-feature tuning; the effort scale is one feature's rule.

### Three things this batch did not do, and why

- **`TERMINAL_STAGES` and `LIVE_STAGE_PRECEDENCE` stayed in `enums.py`.** The packet's code plan said
  they move. They are derived from `CardStage`, which is still shared, and a set split from the enum
  it enumerates is worse than one that waits: all three move in the batch that moves `CardStage`.
- **`sync_commitment_for_stage` lives in `features/cards/use_cases.py`, and it is the Sprint's.** A
  Sprint commitment follows an Action's stage, and every writer of that stage is in that file or
  calls into it. It moves to the Sprint feature when `SprintCommitment` does — recorded here so the
  wrong owner is visible rather than forgotten.
- **`test_card_move_tool_rejects_a_terminal_stage` is not cited by CD-STAGE-011.** The audit table
  said it would be; reading it again, it is about `move` mode reaching a terminal stage, which is the
  stage-ladder packet's rule, not about how a Card is born. It stays green and uncited until then.

### What the scenarios found

`test_card_text_and_blocked_reason_stay_on_one_validated_editor` blocked an **Idea** to reach the
editor. After D2 the Blocked button is not on that screen, and the test stopped at the missing
button — which is the audit's own prediction ("a test that keeps working after the rule changes was
not testing the rule"). It is an Action now, and it cites CD-BLOCKED-010.

Three scenarios had no test at all and now do: an Idea's own parent rule (CD-TREE-003), a title that
is only spaces (CD-TITLE-009), and that no screen anywhere can change a parent — asserted against the
whole `telegram` package, not one screen (CD-TREE-005).

Verification: `ruff check .` clean; `pytest -q` 598 passed / 3 skipped; 0 import cycles;
Rules A–F and K at 0, G at 2, H at 28. `prompt_prefix.json` moved on exactly one line,
`SYSTEM_PROMPT`; `schema.json` untouched.

| | after 5.0 | Phase 5.a |
|---|---:|---:|
| Entity dispatch points outside `features/` (DoD #1) | 28 | 28 |
| Use case base abstractions (DoD #2) | 0 | 0 |
| Modules over 600 lines (DoD #3) | 5 | 5 |
| Re-export-only modules (DoD #13) | 0 | 0 |
| Modules under `src/` | 135 | 139 |
| `domain.py` | 1516 | **1172** |

### Done means

`pytest -q`, `ruff check .`, and `scripts/architecture_metrics.py` — plus: allowlist counts and DoD
numbers may only fall, import cycles stay 0, and any snapshot line that moved is one the batch
declared. 4.a ended at 540 passed / 3 skipped, DoD #1 28, #2 0, #3 5, #13 0, 445 edges, 0 cycles.


## What Phase 5.b, 5.c and 5.d delivered

Three packets approved together on 2026-08-24 and shipped as one batch, because each of them changes
what the other two write: the stage ladder ([brd/card_stages.md](brd/card_stages.md), `CD-STAGE-013`
… `CD-EFFORT-021`), Checks across a Card's life ([brd/checks.md](brd/checks.md), `CH-WRITE-001` …
`CH-DELETE-014`), and what leaves the workspace
([brd/archive_and_delete.md](brd/archive_and_delete.md), `CD-ARCHIVE-022` … `CD-DELETE-025` plus
`VL-DELETE-015`, `TA-DELETE-008`, `SR-DELETE-013`). Thirty scenarios.

### A stage is an Action's field, and a parent shows its branch

Stage joins effort, repeat and Blocked: a Goal and an Idea have none of their own. Only an Action
moves, so `move_card` lost its subtree walk — an Action has no children and no other kind may be
moved, so the recursion had nothing left to visit.

`propagate_ancestors` is now the one walk that writes every derived value. It reads the Actions in
the branch, archived ones included, and writes `effective_stage`, `blocked` and `effort_points` into
the plain columns. No view gained a `CASE` and no column was added: a Goal cannot be given an effort
of its own, so the column was free to hold the one number it does have.

- A branch with no Action shows Backlog and never Done or Cancelled. `manual_stage` on a Goal and an
  Idea is no longer written or read.
- A parent has no `blocked_description`. Several blocked Actions have several reasons, and picking
  one would be Safwa writing the owner's words; the screen quotes each Action instead
  (`blocking_actions`).
- Summing `effort_points` over every row counts each Action again inside each ancestor. Anything
  that wants the real total adds up Actions, and the prompt's `ai_cards` line says so.

`card_progress` kept the completion counts and lost its effort half to the column.

### Three rules replaced eight cases for Checks

A Check hangs on one Card or on none. That ruling **deleted** machinery rather than adding it: the
eligible-Cards walk in `_spawn_check_successor`, the shared-Check survival rule in `archive_subtree`
and `delete_subtree`, and `_has_other_live_card` all existed to hold a Check that several Cards
disagreed about. `CardCheck` took a unique constraint on `check_id`.

- **R1** — a Card closes when every Check series on it was answered at least once **on this Card**.
  `unobserved_series` is that question, and it replaced the flat Pending gate at every door: the
  domain, the Done screen and the proposal guard.
- **R2** — closing deletes whatever is still Pending. The Values that instance carried go back to the
  answered instance of its series rather than out with the row.
- **R3** — reopening puts each plain Check back to Pending and opens one fresh instance of each
  repeating series.

R1 and R3 meet in one place worth naming: an open instance that an answer on this Card opened belongs
to the next cycle and does not hold the Card, which is what `source_instance_id` records. A reopened
Card opens its fresh instance with no source, so the series is unobserved there again — that is the
whole difference between "answered mid-cycle, so Done is allowed" and "reopened, so ask again".

`features/checks/` gained `model.py`, `use_cases.py` and `api.py`. Cards asks that door
`require_check_answers` before it writes anything and `settle_checks` after, and never touches a
Check row itself.

**Two things the model reads were wrong, and one was noise.** `SYSTEM_PROMPT` said "A Card with
Pending Checks cannot complete", which R1 replaced, and listed the Action-only fields without stage.
Both are fixed. Separately, every tool schema carried Pydantic's `title` on every property — the
property name written a second way, with no reason behind it the way the nullable branch has one.
`tool_json_schema` drops it: 11359 characters of mutation-tool schema became 9987, and the board
subagent's whole prefix fell from 16417 to 15128.

**`ai_card_events` left the Advisor's view catalogue.** A 4B model rarely writes a useful query over
an audit log, and the line describing it — `edit_<field>`, `link_<kind>` / `unlink_<kind>` — was the
hardest thing in the catalogue to read. The view itself stays: it is declared, created and
allowlisted, so a saved Request may still reach it, and the `diary` subagent still reads it, because
work done with buttons never reaches the conversation and that log is the only record of it. Only the
Advisor stopped being told about it. `BOARD_PROMPT` never listed it.

**`ai_cards.pending_checks` is gone.** It counted open instances, and under R1 that is no longer the
gate: a Card whose repeating Check was answered once has one open instance and completes anyway, so
the number contradicted the rule stated two lines above it in the same prompt. Nothing in the code
read it. `ai_cards.direct_checks` names the Checks on a Card and `ai_checks.card_id` says how each
one stands, so the fact is in one place instead of two, and the `ai_cards` line is one column
shorter. What the model actually needs at the moment it needs it still comes from the completion
guard, which refuses the proposal with the unanswered titles in a retryable error.

### Archiving got one reason, and everything else is deleted

Archiving exists so the workspace does not fill up. Only a Card and a Check qualify, they go on their
own two Sprints after closing (`ARCHIVE_AFTER_SPRINTS`), and archived is a matter of sight: the
effort and the completion still count. `archive_settled_items` runs where a Sprint ends, which is
also why nothing is archived while the workspace is in Planning.

- `archived_at` came off `Value`, `Tag` and `SavedRequest`, and every `archived_at IS NULL` that
  filtered them went with it — the three `ai_*` views, the context builder, the pickers and the
  screens. `archive_value`, `archive_tag` and `archive_saved_request` are `delete_*` now.
- `RemoveToolInput` is turned inside out: `delete` is the default and takes every entity, `archive`
  is refused for anything but a closed Card or Check. It moved to `features/proposals/remove.py`,
  next to the tool it belongs to, which is why Rule H fell by two.
- Deleting a Card deletes its Checks, except one that carries a Value: that Check is also a
  measurement, so it stays, on no Card. `delete_subtree` deletes every link, commitment and event by
  name rather than trusting the FK cascade, which is a connection pragma the test engine does not set.
- Archiving by hand is refused for a Card that is not closed, and reopening one takes it back out of
  the archive.

### Completion feedback is gone

Torn out whole, as the owner ruled: `FeedbackQueue`, `Card.liked`, `set_feedback`, the `/feedback`
command and its screen, the `feedback` callback, the pending count on `/status`, and the `liked`
inputs to `analytics.py` including the capacity advice. No scenario mentions it, and none may until
the packet that builds it again.

### The move

- `features/checks/model.py` owns `Check`, `CheckOutcome` and the two label maps. `CardCheck` stayed
  in `features/cards/model.py`: the link is written from the Card and recorded in the Card's history.
- `features/cards/model.py` gained `CardStage`, `TERMINAL_STAGES` and `LIVE_STAGE_PRECEDENCE` — the
  three that 5.a deliberately left behind, because a set split from the enum it enumerates is worse
  than one that waits.
- `features/cards/use_cases.py` took `move_card`, `finish_action`, the repeat successor,
  `card_progress`, `archive_subtree`, `delete_subtree`, the archiver and `OperationResult`.
  `domain.py` fell from 1172 to 701 lines.

### Two numbers moved the wrong way, and why

- **DoD #3 rose from 5 to 6.** `features/cards/use_cases.py` is 741 lines. The packets put the Card
  lifecycle there by name, and `domain.py` fell 471 lines to do it; the file holds one feature's own
  rules rather than a layer's. `domain.py` is still 701 and falls under 600 in 5.e, when the Sprint
  leaves it.
- Nothing else rose. DoD #1 fell 28 → 26, #2 and #13 stayed at 0, cycles stayed at 0, Rules A–F and K
  at 0, G at 2.

Both snapshots moved, and both were declared. `prompt_prefix.json` on `SYSTEM_PROMPT`, `BOARD_PROMPT`
and `tool:remove` for what the batch says, and on `DIARY_PROMPT` and all eleven tool schemas for the
`title` that left them — fourteen keys in all. `schema.json` on `cards`, `tags`,
`values`, `saved_requests`, and `feedback_queue` which is gone. The owner rebuilds the database.

Verification: `ruff check .` clean; `pytest -q` 617 passed / 3 skipped; 0 import cycles.

| | Phase 5.a | 5.b–5.d |
|---|---:|---:|
| Entity dispatch points outside `features/` (DoD #1) | 28 | **26** |
| Use case base abstractions (DoD #2) | 0 | 0 |
| Modules over 600 lines (DoD #3) | 5 | 6 |
| Re-export-only modules (DoD #13) | 0 | 0 |
| Modules under `src/` | 139 | 142 |
| `domain.py` | 1172 | **701** |

### Tests the ruling broke

Deleted as business_invalid, each because it asserted a rule the owner removed:
`test_one_check_serves_several_cards`, `test_successor_is_linked_to_live_cards_only`,
`test_terminal_card_never_regains_a_pending_check`,
`test_archive_and_delete_keep_a_check_its_other_cards_still_need`, the Value, Tag and Request archive
tests, the three "writing the name brings the archived one back" tests, and
`test_ai_links_a_check_to_a_second_card_by_title`, which became the one-Card link test instead.

## What the 5.b–5.d review fixed

Three defects the review found, all in what the batch had just written.

**An archived answer stopped counting.** `unobserved_series`, `drop_pending_checks` and
`clone_checks_for_successor` all read `card_checks`, which hides an archived Check. So two Sprints
after a repeating Check was answered, the archiver took that answer off the screens and R1 stopped
seeing it: the Card was refused Done and asked the same question again, the Values of the instance
R2 deleted were dropped instead of handed back, and a repeating Card whose only Check had been
archived gave its successor no Check at all. `series_instances` is what the three rules read now —
every instance on the Card, an archived one included — and `reopen_checks` uses it instead of the
copy of that query it was carrying. `card_checks` keeps its meaning and its one caller: what a screen
shows. Two tests were written first and failed first, CH-ARCHIVE-013 and CH-CLOSE-011.

**Rule J could not see a uniqueness rule.** The schema digest was columns, indexes and foreign keys,
so `CardCheck`'s `UniqueConstraint("check_id")` — the whole of "a Check hangs on one Card" — was
added without moving a hash, and could be dropped the same way. The digest takes unique constraints
now, which is a declared snapshot change: the nine tables that carry one were regenerated, and
nothing about the database itself moved.

**A parent counted the Actions in its branch and never saw its other children.** A Goal with one
Done Action and an Idea that had nothing in it called itself Done, because `derived_from_actions`
walked past the Idea to reach Actions and there were none to find. Archiving that Goal then took the
Idea into the archive in Backlog, and the owner ruled that a Card that is not Done or Cancelled is
never archived, whoever dragged it there.

The lie was the derivation, not the archive, so the guard is not where the fix went. A parent now
adds up its **direct children** — `derived_from_children` — and each child already carries its own
derived values, so the recursion reaches the Actions and an Idea with nothing in it stays in Backlog
and holds its Goal there. Effort and Blocked give the same numbers as before: an Idea's
`effort_points` is already its branch's sum. Two consequences the review had listed separately close
themselves: an archived Card is now terminal by construction, so `archive_subtree` needs no subtree
check, and `archive_settled_cards` stops silently never archiving a Goal that looks finished — the
Goal no longer looks finished. Two tests were written first and failed first, CD-STAGE-015 and
CD-ARCHIVE-024, and both scenarios gained the line the rule made observable.

`SYSTEM_PROMPT` and `BOARD_PROMPT` moved on one word each, twice: what a Goal and an Idea add up to
is the Cards under them, not the Actions. That is the declared `prompt_prefix.json` change; no tool
schema moved.

Verification: `ruff check .` clean; `pytest -q` 621 passed / 3 skipped; metrics unchanged — DoD #1
26, #2 0, #3 6, #13 0, 0 cycles, Rule G 2, Rule H 26.

## What Phase 5.e delivered

Archiving stopped meaning "gone". The packet is
[brd/series_and_archive.md](brd/series_and_archive.md); the scenarios are five new ones —
`CD-REPEAT-026`, `CD-ARCHIVE-027`, `CH-REPEAT-015`, `CH-ARCHIVE-016`, `PR-TARGET-001` — plus one
rewritten line in each of `CD-ARCHIVE-023`, `CH-ARCHIVE-013`, `SR-RUN-006` and `VL-READ-014`.
[`tests/brd/proposals.feature`](../tests/brd/proposals.feature) is a new file, and `PR` is a prefix
in use.

**A list by stage leaves an archived item out. Every other list shows it, marked.** `ai_cards` and
`ai_checks` stopped filtering `archived_at`, and so did the Cards under a Card, the Checks on a
Card, the Cards a Check hangs on, and the answer of a saved Request. The mark is ` [📦]`, rendered
by the same function that renders the repeat marker, so the owner and the model read one wording.

Safwa had been answering a counting question wrong and answering it quietly: with a repeating Check
on a Card, "how many times did I do it" came back 1/1 where the truth was 2/1, because the archiver
had taken the older answers off the view. `ai_checks.card_id` could also name a Card `ai_cards` did
not carry, so a join between the two dropped rows instead of failing.

**The repeat marker names the open instance.** It rendered ` [🔄2]` while both prompts told the
model that number was an id. It is the instance's place in its series, so a model that read it as an
id opened a different item. It is ` [🔄2, live #7]` now, and ` [🔄2]` only when the series has
ended. `REPEAT_MARKER` carries a `{live}` slot that `REPEAT_LIVE` fills, so there is one branch and
one format rather than one of each per outcome.

**A series names itself.** `ai_cards` gained `series_id` and `ai_checks` exposes
`COALESCE(series_id, id)`, because the first instance carries no series until a second one is made —
grouping by it dropped every unrepeated row into one nameless bucket. `ai_checks` also gained
`card_series_id`, which turns "every answer across every copy of a repeating Action" into one flat
query with no join. The `direct_checks` column left `ai_cards`: once `ai_checks` showed the archived
ones, the two disagreed about how many Checks a Card has, and `ai_checks WHERE card_id = N` is the
one answer.

**An archived item opens.** The citation keeps its link, `open` no longer calls one missing, and
`render_card`/`render_check` render it instead of refusing. The archived screen is a branch inside
each render, not a `render_archived_*` of its own: only the controls differ — no field button, no
answer button, no second trip to the archive, Reopen for an Action that does not repeat, Delete —
and a second function would be a copy of the reading half, which is the shape this batch removed
from two other places.

**`require_target` says which refusal it is.** "Does not exist or is archived" was one sentence for
two different failures, and it sent the model looking for an id that was already right. An archived
target is `target_archived` now, and the hint tells the model to name it to the owner as
`[title](card:12)` rather than to propose again.

### What this batch cleaned up

- `card_checks` and `series_instances` were the same query once the filter went; one survives.
- The children screen and the Card screen each carried their own copy of "the Cards under this one"
  and "the Checks on this one". Both call the use case now, so a filter cannot drift between two
  screens again.
- Four `archived_at` filters were deleted because they filtered nothing: an archived Card is always
  terminal, so a query for Today, for the Sprint, or for a non-terminal stage could never see one,
  and an archived Check always has an answer, so a query for Pending could not either.
- **The read authorizer denied a query it should have allowed, and had since it was written.**
  SQLite reports a read with no column name when a view is flattened into a scan that needs none —
  `SELECT count(*)` over any view, or `SELECT id` where the id is the rowid — and it names no view
  to attribute it to. `SELECT id FROM ai_values` failed on a view this batch never touched. The
  authorizer now allows a read that names no column; the statement validator has already refused
  every `FROM` that is not a view. It only surfaced here because dropping the `WHERE` clause left
  `ai_cards` with no column read of its own.

### Found and left alone

- A Check has no delete button on any screen. `CH-DELETE-014` says the owner may delete one, and
  only the `remove` tool does. Older than this batch.
- `render_card` is 250 lines and grew a branch. Splitting its button rows from its reading is the
  obvious next cut, and it belongs to the batch that moves the Telegram adapters into their features.

Verification: `ruff check .` clean; `pytest -q` 632 passed / 3 skipped; `prompt_prefix.json` moved
on `SYSTEM_PROMPT` and `BOARD_PROMPT` and nothing else, `schema.json` byte-identical; metrics
unchanged except the import graph, 574 edges for 572 — the two view modules now import
`constants` for the archive marker. DoD #1 26, #2 0, #3 6, #13 0, 0 cycles, Rule G 2, Rule H 26.

## What Phase 5.f delivered

Approval packet: [docs/brd/heavy_analyzer.md](brd/heavy_analyzer.md). Twelve scenarios,
`tests/brd/heavy_analyzer.feature`, prefix `HAN`.

**The Advisor is offered a helper by the read that needed one.** A `query_safwa` result whose SQL
goes past one flat scan — `JOIN`, `GROUP BY`, `HAVING`, a set operation, `WITH`, a window, a query
inside a query — or whose rows were cut by a cap, carries a notice naming `call_helper`, and the
tool is added to that session's tools there and then. A read that failed offers nothing: its `hint`
already says to repair that one SELECT. Nothing about helpers is in `SYSTEM_PROMPT`, which is what
the owner required, and `is_complex_read` deliberately lets a bare aggregate through.

**`heavy_analyzer` is a mini session, not a routed subagent.** It reads with `query_safwa` and ends
by calling `forward_output` — its last read goes to the Advisor as rows, with the SQL beside them —
or `report_failure` with one sentence. It never speaks: a result of fifty rows cannot be retold, and
a small model retelling numbers is where numbers get invented. Ten reads
(`HEAVY_ANALYZER_MAX_TOOL_CALLS`) bound it.

Because the answer is rows, none of the routing machinery was touched: `route`, `RoutedSubagent`,
`_run_child` and `_routed_context` are unchanged, and no flag was needed to keep the helper out of
the routing rules — it is not in `AGENTS` at all. `call_helper` blocks the Advisor exactly as `route`
does; `/cancel` cancels the turn and the helper with it.

**Each view carries its own documentation.** `SqlView.doc` holds the block a model reads, beside the
SELECT it describes, and `view_catalogue(views, names)` composes the list one reader is given. The
Advisor names nine views, the board seven, the helper all ten. The catalogue existed twice before
and had already drifted; it now exists once per view. `view_catalogue` refuses a name no feature
publishes and a view with no `doc`.

**`ai_card_events` reaches a second reader.** Only the Diary was told the log existed. The helper is
told too — one row per change over time is the shape a question about a stretch of time uses — and
the Advisor and the board still are not. `actor` stays: it names the source of a change, `user_ui`
or `ai`, which is the only distinction left when there is one owner.

**`ActorType.SYSTEM` is gone.** It appeared once in the whole codebase, as a column default that
could not fire because `record_card_event` always passes an actor.

### What this batch cleaned up

- The `{views}` composition removed the second copy of the view catalogue, and gave the board the
  `card_series_id` clause the Advisor's copy had and its own did not.
- Rule I now hashes the **assembled** prompts (`agent:board`, `agent:diary`) rather than the raw
  constants, so the snapshot covers what a model actually reads. `agent:diary` came out at the same
  hash `DIARY_PROMPT` had, which is what says the Diary's own list was left alone.
- The view-name check moved off `BOARD_PROMPT` alone and onto every prompt whose list is composed.
- `tests/test_brd_traceability.py` accepted two-letter prefixes only; `HAN` was silently invisible
  to it. Widened to two or three.

### Found and left alone

- **Automatic archiving writes no `card_event`.** The owner's Archive button records one; the sweep
  that archives what has waited two Sprints records nothing, so the log's `archive` rows are always
  the manual half. Adding the event changes what the log contains, so it is its own batch.
- **`ai_comment` is a Diary field named like a view.** Nothing breaks, but a check that reads view
  names out of a prompt cannot tell it from a view, which is why the Diary's prompt stays out of
  that check. Renaming the field is a schema batch.
- `ai/service.py` is 2286 lines and grew again here. It is the module Phase 7 extracts.

Verification: `ruff check .` clean; `pytest -q` 665 passed / 3 skipped; `schema.json` byte-identical
(Rule J hashes name, type, nullability and key, not a default). `prompt_prefix.json` moved once, as
declared: `SYSTEM_PROMPT` by the two marker lines moving below the catalogue and nothing else,
`agent:board` by the one added clause, plus `HEAVY_ANALYZER_PROMPT` and `tool:call_helper` as new
entries. Metrics: 144 modules for 142 and 580 edges for 574, both the new package; DoD #1 26, #2 0,
#3 6, #13 0, 0 cycles, Rule G 2, Rule H 26 — all unchanged.

## What Phase 5.g delivered

Approval packet: [docs/brd/values_on_a_repeat.md](brd/values_on_a_repeat.md). One new scenario
(`VL-CHECK-016`), one rewritten line (`CD-LINK-012`), and five fixes that needed none.

**A repeating Card hands the Check's Values to the next cycle.** `_spawn_repeat_successor` copied
the Card's Values, Tags, Categories and Energy one loop each, then called
`clone_checks_for_successor`, whose `_copy_check` took `title` and `repeatable` and stopped. The
Value stayed on the answered Check, on the closed Card, archived two Sprints later — so the Check
the owner answered next measured nothing. Reproduced on a real database before the fix: successor
Card values `[1]`, successor Check values `[]`.

`VL-CHECK-012` already stated the rule this broke — a Value is carried by the Check the owner is
still answering, never by a pile of finished ones — so the fix was that rule applied to the path
that forgot it. The move now lives in `_copy_check`, which every path that opens the next instance
goes through, and `apply_check_outcome` dropped its own copy of it. Three paths, one place.

**A Check has a Delete button.** `CH-DELETE-014` says "the owner or Safwa", and only the `remove`
tool implemented it. `check_delete_prompt` and `check_delete_confirm` follow the Card screen's
shape. It is also what an archived Check now offers: since 5.e its screen had only `🔄 Current` and
Back, and no way out at all.

**Three strings the model reads say "board state".** The block holds Values, Tags, the Sprint and
the critical Cards, and carries a `Workspace mode:` line where `planning` means the mode with no
Sprint — one block using the word for two things. Phase 5.a renamed this in code and left the
strings because they move the `SYSTEM_PROMPT` hash. The mode line is untouched.

**`ai_comment` is `remark`.** Every view a model is told about is `ai_*`, so a `diary` tool field in
that namespace gave a small model a reason to try selecting from it. Nothing is stored under either
name, so only the tool schema and the review screen changed.

**"does not exist or is archived" follows the spec.** A Value and a Tag have carried no
`archived_at` since 4.d. `ReferenceSpec.archivable` already existed, so the sentence asks it.

**`_apply_stage_change`'s docstring stopped claiming `finish_action` owns feedback.** Feedback was
removed in Phase 5.

### Corrected

`item_delete_prompt` and `item_delete_confirm` were reported here as untested. They are not:
`test_manual_tag_and_value_delete_unlinks_cards` drives both through the real callback path, for a
Tag and for a Value. The Diary's prompt also rejoined the check that no prompt names a view the
database does not have, which the `ai_comment` rename made possible.

### Found and left alone

- **Automatic archiving writes no `card_event`, and should not.** The log records what the owner did
  on a screen and what an approved proposal did; the sweep that archives what has waited two Sprints
  is neither, `archived_at` already records it, a row per archived Card would grow the log with the
  board, and after 5.f there is no actor left for it to claim. `ai_card_events`'s own documentation
  now says `archive` is always the owner's own.
- `render_card` is 985 lines of module and 250 of function. Phase 8.

Verification: `ruff check .` clean; `pytest -q` 668 passed / 3 skipped; `schema.json` byte-identical.
`prompt_prefix.json` moved on `SYSTEM_PROMPT`, `tool:diary` and `agent:diary` — the diary prompt
names the renamed field twice — and on nothing else. Metrics unchanged from 5.f: 144 modules, 580
edges, 0 cycles, Rule G 2, Rule H 26, DoD #1 26, #2 0, #3 6, #13 0.

## What Phase 5.h delivered

The last business batch of Phase 5, and the one that empties `domain.py` of the Sprint. Approval
packets: [brd/planning.md](brd/planning.md) and [brd/sprint_plan.md](brd/sprint_plan.md), nineteen
scenarios in [`tests/brd/planning.feature`](../tests/brd/planning.feature), `PL-MODE-001` …
`PL-PLAN-019`.

### Three rules the code did not keep

- **A Sprint needs work, not only words.** The Planning screen offered Start only with Success
  criteria and at least one Action in Sprint or Today; `start_sprint` itself accepted an empty plan.
  The owner ruled it a rule, so the domain refuses one now, and eight tests that started a Sprint
  over an empty board had to plan something first — which is what says the refusal reaches every
  door.
- **A Sprint that ends is a conversation, not a receipt.** The expiry poll used to post
  `⏹ Sprint 3 reached its planned end date`, a `RECEIPT`, which is never part of the dialogue Safwa
  reads. Ending a Sprint now writes a six-line summary from the Sprint's own record and leaves it as
  a system Reminder that is already due, so the scheduler hands it over the way a fired Reminder
  hands over its words: the same gate, the same one turn, the same retry when the owner is mid-answer.
  Safwa writes the message, and ends it with `[Sprint retro](retro:12)`.
- **On its last day, ending the Sprint is not early.** The button says `⏹ Finish Sprint` from the
  planned end date onwards.

### What the summary says, and what it does not

Six lines: which Sprint and when it ran, how it ended, its Success criteria, the five effort figures,
how the Actions ended up, and the titles of what is still open (`SUMMARY_OPEN_TITLES = 5`, then a
count). No analysis, no categories, no energy, no advice — Safwa is told how the Sprint went so it
does not go reading tables to find out.

`retro` is a citation type now, and the screen it opens is deliberately empty: the retrospective is
its own feature, and it inherits a link that already works rather than having to add one.

### The retrospective picture is gone

Torn out whole, the way the completion feedback loop was in 5.b: `analytics.py`, `/retro` and its
menu button, `retrospective_data`, `render_retrospective_png`, `retrospective_recommendations` with
the four thresholds nobody chose, the `retrospective_png` message kind, and the `matplotlib`
dependency. Kind code 13 stays out of circulation, beside 1 and 2.

`sprints.capacity_effort_points` went too. It was written at every start and read by nothing; the
capacity is one number in Settings, and `PL-PLAN-018` puts it beside the plan's total on both
screens that show one. Nothing replaced the column: the effort a Sprint started with is its
`committed` figure, which its own commitment rows already answer.

### The move, and the two directions that could not both be doors

`features/planning` owns `Sprint`, `SprintCommitment`, and every use case that starts, ends,
expires or counts one. `domain.py` fell from 710 to 547 lines and is under 600 for the first time.

Cards and Planning need each other in both directions — Cards writes a stage and the Sprint must
record it; the Sprint ends and Cards must archive what settled — and a door each way is a cycle. The
split that has one direction only:

- `features/planning/api.py` is what Cards calls, and it holds the implementations rather than
  re-exporting them: `sync_commitment_for_stage`, `record_sprint_result`,
  `delete_commitments_of_cards` and `settled_cutoff`, which counts in Sprints and so was never
  Cards' question. `features/cards/use_cases.py` no longer names `SprintCommitment` at all.
- `features/cards/api.py` is what Planning reads: the `CardStage` enum, `planned_actions` and
  `action_titles`. It imports the Card model and nothing else, so the chain
  `cards.use_cases → planning.api → cards.api → cards.model` revisits nothing.
- `archive_settled_items` is composed in `domain.py`, where both halves are in reach, and
  `domain.finish_sprint` and `domain.expire_due_sprint` are the two callers that run it. Planning's
  own `finish_sprint` ends the Sprint and hands it over; it does not archive.
- `features/profile/api.py` gained `sprint_length_days` and `capacity_effort_points`;
  `features/reminders/api.py` gained `create_sprint_reminder` and `delete_sprint_reminders`, so a
  Sprint's own warnings and its hand-over are written through the Reminders door instead of by
  setting `system` and `sprint_id` on a row from outside.

**The Telegram adapters did not move.** The packets planned `features/planning/telegram.py` for
`telegram/sprint.py` and `telegram/plan.py`. Both are imported at module level by
`telegram/callbacks.py` and `telegram/commands.py`, so moving them makes `safwa.telegram` import a
feature that imports `safwa.telegram` — the cycle `features/profile/screens.py` is already dodged
with a function-level import. They move in Phase 8 with the rest of the adapters, beside
`render_card`.

### Tests

`tests/test_sprint.py` is the Sprint's own file again: seventeen tests, sixteen of them citing a
`PL` scenario. The renames follow the audit tables in both packets; `test_domain.py`,
`test_telegram_item_ui.py`, `test_checks.py` and the advisor E2E cite the rest. New behaviour got
its test first in each of the three cases above.

Verification: `ruff check .` clean; `pytest -q` 678 passed / 3 skipped. `schema.json` moved on
`sprints` alone, as declared. `prompt_prefix.json` byte-identical — the hand-over is a per-turn
request, not a prompt. Metrics: DoD #1 26, #2 0, #3 **5** (`domain.py` left the list), #13 0,
0 cycles, Rule G 2, Rule H 26.

## What the Phase 5 review fixed: dead code, one query, Rule L

Found by reading Phase 5 against the code rather than against its own summary.

- **Three dead guards.** `finish_action` refused an archived Card, which `TERMINAL_STAGES`
  already refused one line later; `live_repeat_instance_id` filtered `archived_at` twice on a
  query that cannot return an archived row. Both gone. The third, in `move_card`, waits for the
  derived-archive batch that deletes the branch it guards.
- **`stage_actions` was `planned_actions` with the archive filter dropped.** A Card cannot stand
  in Sprint or Today while archived, so the two queries asked the same question. What is left is
  `cards/api.actions_on_stages`, in the feature that owns the invariant, with `planned_actions`
  as the two-stage call and `PLANNED_STAGES` declared once.
- **Nine re-exports in `domain.py`** that no module outside it imported. `domain.py`: 547 → 536.
- **Rule L: a feature never reaches back into `safwa.domain`.** Rule E watches feature-to-feature
  edges only, so a feature taking its *own* use cases through the legacy facade passed unseen —
  and every one of those is a reason `domain.py` cannot be deleted. Nine such imports were
  rewritten to the feature's own module; 17 remain, allowlisted, and the count may only fall.
- **`extra_rows`** was threaded through seven renderer signatures and never given a value.
- **A Check's `series_id` is lazy**, as a Card's already was. Null means "I am my own series", and
  `COALESCE(series_id, id)` is the definition everywhere. The stamp is written in `_copy_check` —
  the moment a second instance exists is the moment the series becomes real — so all three copy
  paths do it once, in one place.

## What the Cue batch delivered

The Sprint's hand-over used to be written as a **system Reminder that was already due**, so the poll
wrapped a six-line Sprint summary in "1 Reminder triggered", told the Advisor to check the named
items with `query_safwa` first, and indented only the summary's first line. The mechanism was named
after its first user, and its second user had to dress up as one.

`src/safwa/cues/` is that mechanism under its own name. A **Cue** is the finished request for one
Advisor turn: whoever had the facts wrote them down, so the Advisor relays rather than goes looking.

- `cues/model.py`, `cues/queue.py` — the `cues` table and `cue_advisor(session, text=...)`. The row
  is **the single record of what Safwa still owes the owner**, written inside the producer's own
  transaction and deleted only once the turn landed.
- `cues/runtime.py` — `CueRuntime`, moved whole out of `features/reminders/telegram.py`: `can_speak`
  (the gate), `still_current` / `release` (the background lease), `speak(text)` (one Advisor turn,
  posted as `MessageKind.CUE`).
- `cues/background.py` — one waiting Cue said per tick, oldest first.
- `finish_sprint` calls `cue_advisor` in the transaction that ends the Sprint. No Reminder is
  created, none is deleted afterwards, and the words are the Sprint's own.
- `MessageKind.REMINDER` → `MessageKind.CUE`, keeping mark code 5: the meaning did not change, only
  the name that was too narrow for it.
- The Cue poll is not a feature's, so `bootstrap/modules.py` puts `CUE_QUEUE` in front of the tasks
  the features declare, and `test_feature_modules.py` asserts exactly that shape.

### `next_fire_at` stopped doing two jobs

The first cut left Reminders delivering through `CueRuntime` directly and keeping its own retry:
`next_fire_at` stayed unadvanced until the answer landed. That works, but it means two different
records mean "not yet said" — a column for Reminders, a row for everything else — and which one a
reader has to look at depends on who produced the words.

Now there is one. `Reminder.next_fire_at` says **when**, and nothing more: a tick writes the Cue and
moves the row on in the same transaction. `features/reminders/background.py` lost its `Gate`,
`Speaker`, `LeaseCheck` and `LeaseRelease` types, its gate call, its lease and its Advisor turn;
`features/reminders/module.py` no longer builds a runtime at all. The poll is what `CLAUDE.md` says
it is — schedule arithmetic.

**One thing waits to be said at a time.** A tick that finds a Cue still waiting writes nothing, and
the due Reminders it would have carried stay due. Without that line, an hour of a busy owner would
become twelve rows and twelve messages the moment they are free.

This also closes a real window: a crash between a successful delivery and `settle`'s commit used to
fire the Reminder twice. There is no such gap now.

**Seven scenario lines were reworded**, each by naming the record that actually holds "not yet said":
`RM-FIRE-012`, `RM-FIRE-013`, `RM-FIRE-014`, `RM-FIRE-015`, `RM-FIRE-016`, `RM-GATE-017`,
`RM-GATE-018`. No rule changed; `fire_count` and `last_fired_at` now count when a Reminder went off
rather than when its answer landed, and the two differ only for as long as a Cue waits.

### Tests

`tests/test_cues.py` — delivery, a shut gate, a turn that did not land, oldest-first, an empty queue
taking no lease, and `finish_sprint` through `tick` to the words that reach the owner.
`tests/test_scheduler.py` reads the `cues` table instead of a fake Advisor: the poll under test no
longer speaks to anyone.

Verification: `ruff check .` clean; `pytest -q` **685 passed / 3 skipped**. `schema.json` moved by
one line, `cues`, as declared; `prompt_prefix.json` byte-identical. Metrics: 0 cycles, DoD #1 26,
#2 0, #3 5, #13 0, Rule G 2, Rule H 26, Rule L 17.

## What the derived-archive batch delivered

A Goal's and an Idea's `archived_at` was written in three places, and cleared in a fourth:
`archive_settled_cards` walked the parents and stamped the ones whose children were all archived;
`archive_subtree` smeared one stamp down the whole subtree; `propagate_ancestors` cleared it again
whenever the derived stage was not terminal; and `move_card` cleared the reopened Card's own.
Four mechanisms for one property nobody had named.

The property is that **a parent's archive is derived from its children**, exactly like its stage,
its block and its effort. `derived_from_children` returns it as a fourth element — the latest
child's stamp when every child carries one, and nothing otherwise — and `propagate_ancestors`
writes it beside the other three, in the same comparison and under the same `version` bump.

- Only an Action is ever stamped. `archive_subtree` takes the branch's Actions and lets the parents
  follow; `archive_settled_cards` keeps the Actions query and dropped the parent loop. `card_events`
  are written where the stamps are, so a Goal no longer records an archive it never performed.
- `settle_archive` is the one place that turns stamped Actions into the branch they changed: every
  stamp is written before the first walk, so a parent reads its siblings as they will be rather than
  as they were halfway through.
- The un-archive branch in `propagate_ancestors` is gone. It is the same formula read the other way:
  one live Card means not every child is archived.
- The guard `if card.archived_at is not None and not reopening` in `move_card` was unreachable — an
  archived Action is always in a terminal stage, so `reopening` was always true.

**One behaviour changed:** archiving every child by hand now archives the parent. It used to take a
Sprint boundary, because only `archive_settled_cards` looked at parents at all.

`Card.archived_at` became `UtcDateTime`. `DateTime(timezone=True)` reads back naive out of SQLite,
so a parent worked out from one stamp off the clock and one out of a row raised
`TypeError: can't compare offset-naive and offset-aware datetimes`. The DDL is unchanged, so the
schema snapshot did not move.

`CD-ARCHIVE-024` gained the case the deleted branch used to hold: a live Card under an archived
branch takes that branch out of the archive, and what was archived on its own stays archived.
`test_cd_archive_024_a_live_card_takes_its_branch_out_of_the_archive` crosses a session boundary on
purpose — that is what makes the two stamps differ.

Verification: `ruff check .` clean; `pytest -q` **686 passed / 3 skipped**; snapshots byte-identical.
Metrics: 0 cycles, Rule G 2, Rule H 26, Rule L 17.
