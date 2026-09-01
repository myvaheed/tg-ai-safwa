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
| 6 | Proposals and the first reactive process | business | **done** — 6.a the typed batch and the reducer, 6.b the proposal use cases, 6.c interruption and autoapproval, 6.d the startup sweeps, 6.e–6.g no status, no tables, no stringly vocabularies |
| 7 | `agent_runtime` | technical + business | **done** — 7.a 21 scenarios approved, 7.b `ai/service.py` deleted, 7.c the interaction contract |
| 8 | `telegram_llm` and `TurnManager` | business + technical | **done** — 8.a the chat leaves Safwa, 8.b the handlers go to the features; criterion 16 carried |
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

## The two files that are read as truth are kept current by deleting

`CLAUDE.md` and `README.md` are read as if every line were true, so a line that stopped being true
costs more than a missing one. Both are corrected **in place**: the wrong line is removed or
replaced, never left standing next to its correction, and neither file gains a paragraph explaining
what it used to say. `tests/test_docs.py` fails on a link either of them carries to something that
does not exist.

`archived_docs/` is not held to this. It records what was intended before the migration, it names
modules that are gone, and it is a source to read intent from — never to quote.

## Completion feedback and the retrospective were removed in Phase 5, and will be built again

The owner ruled on 2026-08-24 that the completion feedback loop — the thumbs-up asked after an Action
is Done — is torn out rather than specified, and designed again from scratch afterwards. No packet
writes a scenario for it, and no scenario may mention it until the one that rebuilds it.

It went with 5.b: `FeedbackQueue`, `Card.liked`, `set_feedback`, `move_card`'s two reopen lines that
cleared them, the `/feedback` command and `render_feedback`, the `feedback` callback, the pending
count on `/status`, and the `liked` inputs to `analytics.py` including the capacity advice.

The retrospective went the same way in 5.h, and the same rule holds for it: it is a feature to be
designed after the migration, not a packet of Phase 5. `retro` survives as a citation type opening a
deliberately empty screen, so whoever builds it inherits a link that already works.

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
time as a dependency. The `state_flow` operators are unconsumed for the same reason: Phase 6
delivered the reducer without one, and the first subscriber arrives in Phase 8.

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
  `cancel_approval_for_proposal` discards every pending proposal in that batch. Inside one
  batch `_refresh_queued_proposal` re-snapshots the expected version and the workspace revision
  together; a Reminder cannot escalate over a pending proposal at all; and `approve_proposal`
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

- `ReferenceSpec` gained `owner`, which makes the linking side a parameter instead of always a Card,
  and the Value screen was given both carriers so it counts Checks **without branching on the entity
  name** — the first attempt did branch, and Rule H caught it.
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

This closes the scheduler's old window: a crash after the Reminder turn but before `settle` could
materialize the same firing twice. The Cue is now the durable hand-over, so the Reminder itself
moves on in the transaction that writes those words down.

**Seven scenario lines were reworded**, each by naming the record that actually holds "not yet said":
`RM-FIRE-012`, `RM-FIRE-013`, `RM-FIRE-014`, `RM-FIRE-015`, `RM-FIRE-016`, `RM-GATE-017`,
`RM-GATE-018`. No scheduling rule changed. At this checkpoint two firing-history columns were
redefined to count when a Reminder went off rather than when its answer landed; the final review
below subsequently removes them because no retained product behaviour needs that history.

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

## What the final Phase 5 review corrected

**One scanner owns the read-only SQL door.** A normal query is still `SELECT … FROM ai_*`; no
qualification or wrapper is required. `scan_statement` walks the statement once and from that one
token stream refuses comments, unsafe keywords, parenthesized bare table names and comma-separated
table lists, and collects the names the `FROM`/`JOIN` allowlist is then checked against. Each of
those shapes could otherwise hide a base table from the allowlist, and separate regular expressions
missed a different one each: a comment, a quoted or unspaced name, a second table after a comma.
Reading them off one token stream also means a keyword counts only outside a string literal, so
`WHERE title LIKE '%update%'` is a search and `ORDER BY joined_at` is a column. SQLite's authorizer
independently refuses attributed base-table column reads; together the two layers also cover
SQLite's columnless `count(*)` form.

**A Reminder fires when its Cue is committed.** The owner confirmed that firing history has no
product use, so both history columns were removed from the row, the Cue request, the Reminder
screen and `ai_reminders`. Scheduling needs only `next_fire_at`; delivery ownership stays with the
durable Cue.

**A Cue retry does not repeat a registered delivery.** Every Cue owns a stable `event_id`, and that
same id is placed in the invisible Telegram marker and the local message registry. If Telegram
delivery was registered but the process stopped before deleting the Cue, the next poll deletes the
Cue without running the Advisor or sending again. This is deliberately not documented as strict
exactly-once across Telegram and SQLite: a process can still die after Telegram accepts a message
but before the local registration commits. Closing that last external acknowledgement window would
require remote reconciliation or an idempotency contract from Telegram, not another local flag.
If an Advisor turn commits a proposal but its Telegram screen raises an ordinary delivery error,
the failed batch is cancelled before retrying; it therefore cannot hold the Cue gate shut forever.
Hard termination after that proposal commit but before either render or exception cleanup remains
part of durable `AgentRun` recovery, which is Phase 7 rather than a second Cue state machine.
The schema snapshot changes on `cues.event_id` and the leaner Reminder row; during development the
database is rebuilt from scratch, as README now states.

**Rule L fell from 17 to 1 without re-export indirection.** Card and Check toggles moved into their
own use-case modules. The generic relationship resolver is `foundation/references.py`; each feature
declares its own specs in `references.py`, a role `docs/FEATURE_MODULES.md` now carries, because a
spec names a toggle and `api.py` cannot import the Card use cases without closing a cycle through
Planning. A `ReferenceSpec` is eight fields: `key` yields `value_id`, `value_ids` and `value_query`,
and `owner` yields the junction row's other column. Who carries a Value is a question the item
screen answers, so `telegram/_core.py` holds the carriers and no spec names another. Repeat lookup
is read through each feature API. The only
remaining edge is `features/planning/background.py → domain.expire_due_sprint`: it composes
Planning's clock with both the Card and Check archive sweeps, and moving that composition now would
cross the application-composition work reserved for the later cleanup rather than finish a Phase 5
feature move. Current metrics: 156 modules, 638 edges, 0 cycles, Rule G 2, Rule H 26, Rule L 1;
DoD #1 26, #2 0, #3 5, #13 0. Verification: `ruff check .` clean; `pytest -q`
**728 passed / 3 skipped**, with `test_pl_start_005` deselected: it compares a Sprint's local
start date against a UTC one and fails every evening, on this branch and on `main` alike. The prompt hashes moved only for readers that carry the
`ai_reminders` catalogue; the declared schema snapshot moved on `cues.event_id` and the removal of
Reminder firing-history columns.

`docs/brd/` remains temporary migration working material. The binding scenarios are under
`tests/brd/`, and the whole packet directory is deleted when the refactoring migration ends.

## Before starting Phase 6

The plan's §9.2 names `ProposalService` the god-class and cites `service.py:2663`. That reference is
stale: `ProposalService` was 46 lines in the since-deleted `ai/service.py`, and
the god-class is `AIAdvisor`. Read this section instead of that row.

### The process, and where its identity is stored

The §9.1 gate asks for the identity and where it is kept. It is the approval batch, today an
`AgentStep` row with `kind="approval_batch"` and four untyped keys in `metadata_json`: `status`,
`queue`, `tool_calls`, `repair_exhausted`. Not one legal combination of them is written down. Three
writers touch it, all inside `AIAdvisor`: `_materialize` creates it, `resolve_approval` walks the
queue, `cancel_approval_for_target` freezes it. `_pending_batch_for_target` finds it by scanning the
agent's step log in JSON, bounded by `SUSPENDED_BATCH_LOOKUP_LIMIT`.

That is `GenerationGuard` from §9.3, durable. The Manager is justified.

### Three decisions taken before the phase opened

- **The reducer is built now, the `StateFlow` is not.** `foundation/state_flow.py` is imported by no
  module under `src/`. The subscriber appears in Phase 8, where the host translates a proposal state
  into a `TurnAction`; a published flow with no reader until then is flexibility nobody asked for.
  Phase 6 delivers the frozen union state and a pure `reduce`, and Phase 8 adds `as_state_flow()`.
- **Superseded on 2026-08-29: the batch is not a row at all.** It was given its own table to get it
  out of `AgentStep`, which removed the JSON scan and its magic limit. Nothing then read it across a
  restart, so it moved into `ProposalStore` with the review it suspends, and the scan is gone with
  the table.
- **Two scenario packets, not one.** `docs/brd/proposals.md` covers the lifecycle; the interruption
  and autoapproval rules are their own packet. Each is approved on its own.

### The seam with Phase 7

`resolve_approval` is two functions in one, and the batch that blurs the line has swerved into
Phase 7.

- **Phase 6 owns** the batch record, the decision reducer, `prepare`, `approve`, `delete`, the
  stale check, process-start cleanup, and finding a batch by its target.
- **Phase 7 owns** `_claim_session`, `AgentSession.restore`, `_resumed_transcript`,
  `_run_agent_loop` and `_finish_run`.

The handover is "the queue is empty, here are the resolved `tool_calls`". A Phase 6 batch that
touches `AgentSession` has crossed the seam.

### One exit criterion is already met

"Autoapproval calls the *same* confirmation use case" holds today: `_advance_autoapprovals` calls
`resolve_approval` with `apply_proposal=True`. What is missing is the scenario, not the code.

### The batches

| # | What | Kind | State |
|---|---|---|---|
| 6.a | Packet one; the models move; the batch becomes a typed record; frozen state, `reduce`, table-driven transition tests | business | done |
| 6.b | `prepare_proposal`, `approve_proposal` and ending a review as use cases; `ProposalService` deleted; `callbacks.py` and `_materialize` call them | business | done |
| 6.c | Packet two; interruption and autoapproval over the same reducer; `decide_batch_item` and `interrupt_batch` cut at the seam | business | done |
| 6.d | startup invalidates process-local proposal state without writing the feature's tables directly | technical | done |
| 6.e | Neither row carries a status: existence is pending, closing is `state.head is None`, and the enums reach the adapters instead of being coerced from strings | technical | done |
| 6.f | `ProposalStore`: a review and its batch are process state, so the three tables go and startup only clears what pointed at them | technical | done |
| 6.g | The vocabularies are closed: `ChangeAction` replaces the action strings, `BatchTargetType` and `BatchToolStatus` go as dead generality, and one table spells the receipt line | technical | done |

Target shape, using the file names the rules actually scan:

```text
features/proposals/
  model.py       ChangeProposal, ProposalChange, ApprovalBatch as values, and the frozen
                 BatchState, QueueItem, BatchAction and BatchEffect unions  (Rule C reads this)
  reducer.py     reduce(state, action) -> (state, effects), no I/O   (Rule D reads this file)
  store.py       the reviews and batches this process still owes an answer to
  use_cases.py   prepare, approve, open, interrupt, number and refresh the queue
  api.py         unchanged, plus ProposalChange for the features that handle one
```

There is no `manager.py`: nothing subscribes to this state, so the writer half of Rules C and D
scans no file here. It starts biting when Phase 8 adds the subscriber.

### Rules C and D stop being vacuous

Both read 0 before this phase because the codebase had no reducer at all. Rule C now checks seven
frozen classes in `proposals/model.py`, and Rule D every function in `proposals/reducer.py` — the
entry point is a `match` that hands the work to private helpers, so checking `reduce*` by name
would have left those helpers free to open a session.

### What the numbers should do

`ai/service.py` 2286 lines, target under 1800; DoD #3 from 5 to 4. `schema.json` moves on the three
tables that migrate. **`prompt_prefix.json` moves only for a prompt this batch declares it is
rewriting** — a hash that moves without one means something volatile reached `messages[0]`; find it
rather than regenerating.

`tests/e2e/test_advisor_flow_e2e.py` is the insurance for Phases 6 to 8. It is added to, never
traded for a faster unit test.

### What the first packet settled

[brd/proposals.md](brd/proposals.md) is packet one, written and ruled on 2026-08-28. Two things
changed with it, and one was withdrawn.

- **Superseded on 2026-08-29:** proposal age is not a business rule. A proposal remains valid for
  the lifetime of its creating process, then startup invalidates every unanswered review. Proposal
  callback actions ignore generic token age while that process lives.
- **A proposal keeps the right to hold several edits.** `ChangeProposal.changes` is an ordered list
  and the screen has a branch that lists every one; nothing adds a second today. Kept, because if
  one ever holds several the owner has to see all of them before deciding.
- **Withdrawn:** every screen in a queue repeating Safwa's prose for the whole request. That prose
  is the plan, and seeing it again shows what is still left.

The packet's own reachable trigger for a stale proposal is worth carrying here, because it is the
one case where the board moves with nobody present: a Sprint closes itself once it passes its
planned end date, on a background poll, and closing it also archives what has settled. A review
screen is dismissed only by something the owner does, so one left standing overnight is still there
in the morning, waiting on a board that moved without it.

### What Phase 6 delivered

`features/proposals/` owns the whole of a proposal now: `model.py` holds `ChangeProposal`,
`ProposalChange` and the `ApprovalBatch` row beside the frozen `BatchState`, `QueueItem` and the
action and effect unions; `reducer.py` holds `reduce` and its two transitions; `use_cases.py` holds
prepare, approve, delete, opening a batch, the two batch resolutions, the queued-proposal snapshot
and startup cleanup. Rule C reads the frozen classes in `model.py`, and Rule D every
function in `reducer.py` — a transition behind a private name is checked like the rest.

Neither carries a status. A proposal exists exactly while it is pending, and so does its batch:
both end where they end, and a restart ends whatever a crash left. A batch is therefore over exactly
when no screen is still waiting, which is `state.head is None` — the one reading, in
`decide_batch_item`, that decides whether it closes.

Then the tables went too. A review was never read by anything but the process that opened it: no
recovery restored one, and the owner-facing rule is that a restart ends every unanswered screen. So
`change_proposals`, `proposal_changes` and `approval_batches` are gone, and `ProposalStore` holds a
`ChangeProposal` with its ordered `changes` and an `ApprovalBatch` with its `BatchState`. `AIAdvisor`
owns one (a caller may inject a shared one), which is what makes `advisor.reviews` the one place a
screen, a callback and the Cue gate all ask.

An effect is a row to write, never a reading of the state returned beside it, so `reduce` emits
`ResolveCallsEffect` and `RejectPendingEffect` and nothing else: which screen comes next, and
whether the batch closed, are both `state.head`. `decide_batch_item` hands that state on instead of
a flattened target, which is what keeps closing decided in one place.

`recovery.py` has no proposal table left to clear. What it does clear is what a review left in the
database: the buttons that would still claim — nothing else would remove them, because a proposal
button is exempt from the callback-token age — and the `related_id` of the `APPROVAL` screens, which
would otherwise name a review the new process makes later. `_materialize` opens its batch through
`open_batch` and heads its queue through `number_queued_proposals`. Nothing outside the package
opens or ends a review.

`BatchDecision` and `ChangeAction` are what the adapters and the handlers pass, not strings each
site spells out: `resolve_approval`, `has_pending_approval` and `continue_agent_approval` take a
decision, and every `if change.action == "create"` is a member comparison. `AgentChange.action` is
`ChangeAction`, so a change carries the enum from the tool call to the write; a tool still declares
its own `mode` literals, which is how a tool offers only the actions its entity has.

`BatchTargetType` is gone. Its second value was `draft_bundle`, and the module that wrote one was
deleted long before this phase: a queue item is a proposal id. `BatchToolStatus` went with it — the
`status` on a recorded tool call was written twice and read nowhere, because `QueueItem.decision` is
where a screen's answer lives. So is the `"rejected"` result status, which nothing had written since
`ProposalStatus` was removed.

Which changes need a second confirmation moved to the feature that owns them —
`CardProposalHandler.destructive_actions`, read through `ProposalRegistry.needs_confirmation` — so
`callbacks.py` no longer spells out `("card", "delete")`. The Saved/Discarded/Failed line is written
once, in `DECISION_RECEIPTS` beside the decision it reports, and `RECEIPT_MEANINGS` is built from the
same four constants instead of repeating them in `constants.py`.

The seam with Phase 7 held: `decide_batch_item` and `interrupt_batch` return what happened to the
batch, and claiming and resuming the paused session stayed in `ai/service.py`.

`ai/service.py` went from 2286 lines to 2100 across the phase, the module graph from 656 edges to
651, and cycles stayed at 0. **Two of the phase's targets were missed**: `ai/service.py` is not
under 1800, and DoD #3 is still 5. Both are `AIAdvisor`, which is the god-class §9.2 meant, and
Phases 7 and 8 are the ones that take it apart.

Two scenario packets were approved: [brd/proposals.md](brd/proposals.md) and
[brd/proposals_interrupted.md](brd/proposals_interrupted.md), 22 scenarios in
`tests/brd/proposals.feature` and one in the new `tests/brd/screens.feature`.

## Before starting Phase 7

Phase 6 closed on the seam it declared: the batch belongs to `features/proposals`, and claiming and
resuming a paused session are Phase 7's. This section is what Phase 7 starts from; the interruption
decisions themselves are in §"Phase 7 notes" below and are not derived a second time.

Phase 7 in one line: the agent loop stops being Safwa's and becomes `agent_runtime`, a package that
runs a model session for any host — and the rules that loop has been keeping become approved
scenarios for the first time.

### Two batches, split where approval is

| # | What | Kind | State |
|---|---|---|---|
| 7.a | Both scenario packets and the behaviour they fix: 21 scenarios into `tests/brd/agents.feature`, and words typed over a screen resume the request that opened it — the one-turn grace and the four mechanisms around it deleted | business | done |
| 7.b | The §12.5 cut, finished: `agent_runtime` takes the session and the loop behind its ports, the rest of `ai/service.py` lands in five named files, `ai/service.py` is deleted, and `examples/note_keeper/` proves the border | technical | done |

The split is the one the phase actually has. A business batch needs the owner before its tests are
written; a technical batch needs nobody. Cutting either half smaller only creates states where an
old path runs beside a new one, which §15.1 forbids anyway — so 7.b is one move, and a 7.b that
cannot be finished is rolled back whole rather than merged half-done.

**7.a goes first.** It **deletes** five mechanisms. Doing it first means 7.b moves code that has
settled; doing it last means moving the wrong shape into a package that was meant to be finished,
and fixing it there.

Both packets write into the same `tests/brd/agents.feature`, because one `.feature` file is one
package. That is the other reason they are one batch.

### What 7.b has to end with

**`src/safwa/ai/service.py` does not exist when the batch is over.** Not "is smaller", not "is under
900" — the plan's §12.5 cuts it into parts, and the parts have addresses. An earlier draft of this
section restated DoD #3's escape hatch instead, which would have let the hardest file in the
codebase survive the phase intact. It does not survive it.

§12.5 has five rows. Two are already landed — the prompts went to `features/*/agent.py` in Phases
3–5, and `ProposalService` with its `_apply_*` went to `features/proposals/use_cases.py` in Phase 6.
Three are this batch. §12.5 was written against the 3062-line version and names no home for three
regions that outlived it, so those are given addresses here.

Every function in the file, by destination. Line counts are today's.

| Destination | What moves | ≈ |
|---|---|---|
| `agent_runtime/` | `AgentSession`, `AgentLoopResult`, `_run_agent_loop`, `_provider_turn`, `_run_child`, `_deliver_to_parent`, `_execute_route_tool`, `_resumed_transcript`, `_claim_session`, `_finish_run`, `_root_run`, `_close_unfinished_children`, `_resume_interrupted_child`, the two provider loggers, `_json_safe`, `_flatten_content`, `_log_preview`, `_system_note`, `_append_user_message`, `_cache_breakpoint`, and the start-or-resume half of `handle` / `_resume_interrupted_turn` | 787 |
| `ai/advisor.py` | `AIAdvisor.__init__`, `_tools_for`, `_read_specs_for`, `_answer`, `_answer_or_deliver`, `resolve_approval`, `cancel_approval_for_proposal`, `has_pending_approval`, the Safwa half of `handle`, and the tool-schema constants | 380 |
| `ai/messages.py` | `_context_messages`, `_routed_context`, `_session_messages` — the block contents, which are Safwa's | 145 |
| `foundation/errors.py` | `failure_reason` | 5 |
| `ai/tools.py` | `_execute_query_tool`, `_execute_open_tool`, `_execute_read_tool`, `_execute_call_helper_tool`, `_should_offer_helper`, `_execute_mutation_tool`, `_mutation_repair_details`, `_validation_error_summary`, `_add_notice`, `OPENABLE_MODELS` | 315 |
| `ai/materialize.py` | `_materialize`, `_autoapproval_candidate`, `_advance_autoapprovals`, `PendingTool` | 208 |
| `features/proposals/render.py` | `_approval_change_label`, `_approval_results_summary`, `_safe_approval_results_summary`, `_compose_display_outcome`, `_with_queued_siblings`, `_resolved_tool_result`, `_raw_details`, `_proposal_display_line`, `describe_proposal`, `_proposal_result_details`, `_only_change`, `_RESULT_RECEIPTS`, `AUTOAPPROVED`, `_DECISION_NEXT_STEPS` | 280 |

Four decisions the table encodes, each of which could have gone the other way:

- **The tool adapters cannot go into the package.** `_execute_query_tool` knows the `ai_*` views,
  `_execute_open_tool` knows Safwa's item kinds, `_execute_mutation_tool` knows what a proposal is.
  Rule F forbids all three inside `agent_runtime`. They are the host's side of the tool port, and
  `ai/tools.py` is where a host keeps them.
- **The receipt rendering goes to the feature that owns a review.** Phase 6 left it in
  `ai/service.py` on the reasoning that "the labels belong to the session layer". The session layer
  is leaving for a package that must never learn what a proposal is, so the reasoning expires with
  it. New file rather than `features/proposals/api.py`, which is already 606 lines. It takes plain
  dicts, and the only `ai/` module it may name is `ai/contracts.py` — the shared change vocabulary
  every feature already imports — or the graph gains a cycle.
- **`_route_receipt` splits.** The *shape* a child hands its caller — `subagent`, `outcome`, `did`,
  `text`, `error` — is the runtime's, because every host needs one. What fills `did` is
  Safwa's proposal rendering, handed in.
- **`AIOutcome` and `AIOutcomeKind` stay in `ai/advisor.py` for now.** They are a turn's result, and
  §12.4 gives turns their own package in Phase 8 (`features/turn/`). Moving them twice is worse than
  moving them late.

`_context_messages` is the one §12.5 row that is half right: it says the context builders go to
`agent_runtime/context.py`, but `_context_messages` assembles Safwa's board state and memory. What
crosses is the **ordering mechanism** — `_system_note`, `_append_user_message`, `_cache_breakpoint`,
and the rule that only `messages[0]` is a system message. What the blocks contain stays in Safwa, as
`ai/messages.py`, which step 3 landed and step 4 splits along that line.

`failure_reason` is the one row that moved off the map on purpose. §12.5 put it in the runtime
because it sat beside the loop, but it turns any exception into one owner-readable clause and
Telegram uses it, so it went to `foundation/errors.py` — which also stops `telegram/proposals.py`
reaching into `ai/service.py` for it.

Target shape of the package, using file names the rules actually scan:

```text
agent_runtime/
  model.py       AgentDefinition, the frozen RunState / RunAction / RunEffect unions,
                 InteractionRef, the tool outcome union, and the receipt shape
  reducer.py     reduce(state, action) -> (state, effects), no I/O
  loop.py        the provider-and-tools loop, driven by the ports
  manager.py     start-or-resume, the claim, suspension, and the routed chain
  ports.py       SessionStore, ToolRunner, ContextBuilder
  context.py     the block order and the cache breakpoint
  testing.py     InMemorySessionStore
```

The provider comes from `llm_gateway`. Nothing here imports `safwa`, aiogram, SQLAlchemy or Pydantic,
and Rule F says so on every run — it already names `agent_runtime`, so the guard is armed the moment
the package exists.

Two rules in today's loop go different ways, and the split is the test of the boundary.
`route_is_not_shared` is the runtime's: a suspended response cannot carry results for its siblings,
whatever the host is. `mixed_read_and_mutation_tools` is Safwa's: it is a statement about proposals,
so it becomes a decision the tool port hands back.

### The order 7.b is done in, and why

A phase ends in a state that can be kept, and no step may leave an old path running beside a new
one. These five are each green on their own.

1. **`features/proposals/render.py`** — a pure move of leaf functions. Nothing calls back into `ai/`.
   Landed as v6.2.
2. **`ai/tools.py`** — a pure move. The adapters already take `(agent, call)` and return a result.
   Landed as v6.3, with `ToolSession` naming what a tool call may touch on its session, so the
   adapters never import the session and step 4 finds its port already cut.
3. **`ai/messages.py`** — the context prefix, as `ContextBuilder`. Landed as v6.4.
4. **`agent_runtime/`, `ai/advisor.py` and `ai/materialize.py` together.** What is left in
   `service.py` after 1–3 is the session, the loop, the proposal seam and the host around them, and
   they separate in one step: the package gets the loop behind its ports, `advisor.py` gets the
   host, `materialize.py` gets the seam. This is the step that cannot be cut smaller — cutting it
   leaves two loops running at once.
5. **`ai/service.py` is deleted**, and the three production importers and twelve test modules are
   repointed. `examples/note_keeper/` lands here, because until the imports are clean the package
   is not provably standalone.

Steps 3 and 4 changed once the code was measured. The draft made `ai/materialize.py` step 3 and
called it a pure move; it is not one. `_materialize` calls `_run_agent_loop`, recurses into itself
after a repair round, and reaches `resolve_approval` through `_advance_autoapprovals`. Cutting it out
before the runtime exists would mean handing it three callbacks into the advisor, and step 4 would
delete all three. The repair round is the runtime's; what stays Safwa's is preparing the changes and
opening the batch, and that split is legible only once there is a port to split against.

What took its place is the one region that was separable: `_context_messages`, `_routed_context` and
`_session_messages`, with `_system_note`, `_append_user_message` and `_cache_breakpoint`. It is also
where the prompt prefix is actually built, so it is the move most likely to break criterion 10 —
which is a reason to do it early and alone, not late and mixed in.

**Run `tests/test_architecture.py --snapshot-update`-free after every step, not at the end.** Each of
the five touches message assembly, and §12.5's own extra constraint is that `messages[0]` and the
context blocks do not move by a single byte. A prefix that drifts in step 2 is cheap to find and
expensive to find in step 5.
### Exit criteria, all of them checkable

| # | Criterion | How it is checked |
|---|---|---|
| 1 | `src/safwa/ai/service.py` does not exist | the file is gone; no import of it remains anywhere |
| 2 | No module under `src/agent_runtime/` imports `safwa` | Rule F, already armed |
| 3 | No public name in the package mentions `Proposal`, `proposal_id`, `aiogram` or `sqlalchemy` | a test that reads `agent_runtime/model.py` and `ports.py` |
| 4 | The runtime starts on `ScriptedProvider` and an in-memory store | `examples/note_keeper/` runs, imports no Safwa — DoD #8 |
| 5 | No general loop is left in Safwa | `ai/advisor.py` calls the manager and holds no `while True` over provider turns |
| 6 | Largest Safwa module ≤ 400 lines | `scripts/architecture_metrics.py` |
| 7 | DoD #3 falls 6 → 5 | the same; `telegram/callbacks.py`, `telegram/cards.py`, `features/cards/use_cases.py`, `history.py` and `features/proposals/api.py` are Phases 8–9 |
| 8 | Rule H 25 → 24 | `service.py:1218`'s branch on `card` dies with the loop; `OPENABLE_MODELS` does not, and folds with `telegram/screens.py` in Phase 8 |
| 9 | Cycles stay 0 | the same |
| 10 | `prompt_prefix.json` byte-identical | Rule I, run after every step |
| 11 | `schema.json` unchanged unless `agent_steps` is decided out | Rule J |
| 12 | All 21 `AG` scenarios still cited and green | `tests/test_brd_traceability.py` |
| 13 | The two E2E suites are added to, never traded | review of the diff |

### Three risks named before the batch opens

- **`state_json` is a wire format.** `AgentSession.state()` and `restore()` read and write the keys
  in `agent_runs.state_json`. When the runtime owns that shape the keys must not drift, or a session
  suspended before the change cannot be resumed after it. Pre-release this costs the owner one
  recreated database, which is acceptable — but it has to be a decision, not a surprise.
- **The prefix is the fragile thing, not the loop.** Every step reassembles messages. Criterion 10
  is the one most likely to fail, and it fails silently in behaviour and loudly only in the snapshot.
- **`features/proposals/render.py` must not import a session module.** It renders from plain dicts
  the caller hands it, and names only `ai/contracts.py`. An import the other way turns a clean move
  into the first cycle this codebase has had.

### Three questions 7.b answers, with the recommendation

- **`agent_steps`: a runtime observer port, or gone?** Nothing under `src/` reads it and it costs
  five commits a turn, which argues for deleting it. Against that, it is the only record of the SQL
  a local model wrote and the rows it got back. Recommendation: keep the trail, make it one optional
  observer the runtime calls and Safwa implements, and write it once at the end of a turn instead of
  five times inside it. It is the only reason `schema.json` may move in this phase.
- **`ai/mini.py`: one loop or two?** A mini session is the same loop with no suspension and terminal
  tools instead of an answer. SUBAGENTS_PLAN ruled it stays narrow rather than being made resumable,
  and that ruling predates there being a runtime to fold it into; §12.4 already sends its
  `ReadToolSpec` to `agent_runtime/model.py`. Recommendation: fold it as an `AgentDefinition` that
  declares no interaction; if that costs more than the duplication, keep two loops and name why.
- **`AgentRunStatus` has seven members, and `claimed_at` has one reader left.** `agent_runs` is
  durable and recovery reads its status, so unlike a proposal it keeps one — but `cancelled` stopped
  being written in 7.a, and `abandoned` and `interrupted` are now written by recovery and by the
  turn that ends its own children. Same reading for `claimed_at`: nothing contends for it, and what
  is left is one boolean the Cue gate reads. Phase 6's rule decides both — a vocabulary keeps only
  the members something writes and something reads.

### Facts checked before the phase opened, so they are not checked again

- `agent_steps` is written five times a turn — `route`, `helper`, `read`, `read_query`,
  `mutation_intent` — each in its own transaction, and read by no module under `src/`. Only two
  tests read it.
- `recover_startup` already ends every session at boot: `running` becomes `interrupted`,
  `awaiting_approval` becomes `abandoned`, and the claim is released. So SUBAGENTS_PLAN's "session
  clock" is the process lifetime, exactly as a review's has been since Phase 6. There is no age to
  write and no sweep to build.
- "A subagent opens with a tool call" is `tool_choice="required"` on the first turn only
  (`_provider_turn`), and `Settings.ai_tool_choice_required` switches it off for a provider that
  cannot take it.
- `MAX_TOOL_CALLS = 64` and `repair_rounds` ride in `state_json` and come back through
  `AgentSession.restore`, so the budget already survives a suspension. Nothing new is needed for it.
- **The claim guards a race nothing can cause.** Two answers to one screen cannot arrive at once: a
  button is a single-use `CallbackToken`, and a callback is refused outright while `guard.active`.
  The one path that could contend is `_resume_suspended` racing a Save on the same session — and
  7.a deletes it. `claimed_at` is still read elsewhere, by `CueRuntime.can_speak` as "a session is
  running right now", so the column has a second reader either way.
- The assumption §"Phase 7 notes" said to measure is already mitigated: 6.c added a line to the
  `board` and `diary` prompts telling them to say what they are about to do before calling a tool.
  Measure it in 7.a anyway, before declaring it holds.
- `foundation/state_flow.py` is still imported by no module under `src/`.

### Two decisions taken before the phase opened

- **Neither the frozen `RunState` union nor `as_state_flow()` is built.** The plan asked for both;
  7.c dropped the union too, after looking for its reader and not finding one. The loop reads a
  mutable `AgentSession` and the host reads a `TurnOutcome`, so eight state classes, a `RunAction`
  set and a reducer would be an abstraction with no caller — the one thing CLAUDE.md's "Simplicity
  First" forbids outright. `InteractionRef` is the part of that design that does have a reader, and
  it is built. Phase 8 revisits the rest when the subscriber exists.
- **The runtime suspends on an opaque reference and nothing more.** It has no interaction port, it
  never learns what a proposal is, and it does not import Telegram. It can stop with a reference and
  be resumed with `(reference, value)`; the host owns the mapping. That is what makes it a package
  rather than Safwa with different imports.

### What Phase 7.a delivered

`tests/brd/agents.feature` carries 21 approved scenarios under the `AG` prefix — the first time the
rules the agent loop has been keeping were written down anywhere a test can cite. Eleven were
already held by tests that stood without a scenario to point at, and those were cited where they
were; ten needed a test, and six of those needed the behaviour to change first.

**The owner's words now continue the request they were typed over.** An interruption used to throw
that request away and start a second one from the same words, and five mechanisms existed to soften
it. All five are gone:

- `_resume_suspended`'s reach — any saved session of that kind, from any earlier turn — is now
  `_resume_interrupted_child`, this run's own unfinished child of that kind. The one-turn window was
  the mechanism; being the caller is the property that replaced it.
- `_close_lapsed_sessions` is `_close_unfinished_children(run_id)`, called at the moment the turn
  answers or fails rather than swept for on the next turn.
- `cancel_approval_for_proposal` no longer sets the running request to `cancelled`, and no longer
  walks the chain cancelling every caller above the interrupted subagent. Nothing is cancelled: the
  batch ends, the screen freezes, and the request keeps its turn.
- The hint that would have warned the next turn about a proposal it never saw has nothing to warn.

`handle` decides start-or-resume itself, the way `route` already decided start-or-restore: a root
session left with an unanswered `route` and an interruption recorded on it is claimed and continued,
and its pending call is answered with what was proposed, what was refused, what was already saved,
and the owner's words. A subagent is left `interrupted` — unfinished rather than waiting, because no
screen is open on it — and its transcript gains one line saying the owner refused **and wrote
instead**. On "rejected" alone it reads its own record and proposes the same thing again.

**Two scenarios were dropped while the packets were being read, both duplicates.** "The same paused
work is never picked up twice" described a race nothing can cause: a button is a single-use record
and a callback is refused outright while a request is running, so the claim guards nothing the owner
can observe. `test_a_session_can_only_be_claimed_once` stays as an invariant test, and whether
`claimed_at` survives at all is a question for 7.b. "What was already saved is still saved" was
PR-INTERRUPT-017 and PR-INTERRUPT-018 word for word.

**Two the owner asked for were added.** AG-TURN-010 — one request at a time, words that arrive
during one join it, `/cancel` the one thing always available — and AG-TURN-015, the Cue gate. That
gate had been reached only through a stub: the `PL-END-015` tests drive a `Recorder` whose gate is a
boolean, so nothing had ever asked `CueRuntime.can_speak` its three questions.

Two tests carried the old design and were decided rather than edited quietly:
`test_words_over_a_screen_end_the_caller_but_not_the_draft` asserted the cancelled request and was
deleted as `contradictory`; `test_a_correction_reaches_the_session_that_wrote_the_refused_day` kept
its rule and was rewritten to the new flow.

`ai/service.py` went from 2100 lines to 2193 — resuming an interrupted turn is a path that did not
exist, and what it replaced was smaller. The module graph, the cycles, the rule violations, the
prompt prefix and the schema all held still. 773 tests pass, 3 skipped.

### What Phase 7.b delivered

**`src/safwa/ai/service.py` does not exist.** It was 2193 lines when the batch opened and it was
deleted in five steps, each green on its own.

| Step | What moved | `ai/service.py` after |
|---|---|---|
| v6.2 | `features/proposals/render.py` — the receipt rendering | 1928 |
| v6.3 | `ai/tools.py` — the tool adapters, and the tool port | 1546 |
| v6.4 | `ai/messages.py` — the context prefix, as `ContextBuilder` | 1445 |
| v6.5 | `agent_runtime/`, `ai/advisor.py`, `ai/materialize.py`, `ai/runs.py`, `ai/outcome.py` | gone |

`src/agent_runtime/` is 1239 lines across seven modules: `model.py` (the session, the run record,
the receipt), `ports.py` (four protocols and one optional observer), `loop.py`, `manager.py`,
`context.py`, `testing.py` and the package's `__init__.py`. Rule F is 0, and
`tests/test_agent_runtime.py` adds the check Rule F cannot make: no public name in `model.py` or
`ports.py` may say `proposal`, `aiogram`, `sqlalchemy`, `safwa`, `telegram` or `pydantic`.

**The ports are four questions, and the runtime asks nothing else.** Where a session's state lives
(`SessionStore`); what a kind of session may call and what happens when it does (`ToolRunner`); what
it reads before its own steps (`ContextSource`); and what a finished turn means (`Materializer`).
`Observer` is the fifth and the only optional one — a host that keeps a trail implements it, and
`agent_steps` is Safwa's implementation of it rather than a table the runtime knows about.

**One shape replaced five copies of it.** `handle`, `_deliver_to_parent`, `_run_child`,
`_resume_interrupted_turn` and `resolve_approval` each ran the loop and then repeated the same
sequence: store what a suspended session needs, stamp the record, materialize, and decide whether the
turn is over. That is `AgentManager._complete`, once.

**The repair round became a fact about the loop instead of a recursion.** `_materialize` used to call
`_run_agent_loop` and then call itself. `Materializer.materialize` now answers `None` for "I corrected
this turn's tool results, run it again", and the manager loops. The cycle between the seam and the
loop is gone, and with it the two-phase construction that would have been needed to cut them apart.

**`examples/note_keeper/bot.py` is the proof the border holds.** A note keeper in 180 lines: a
search that runs inside the turn, a note that waits for a person, and a resume — on `ScriptedProvider`
and `InMemorySessionStore`, importing no Safwa. It is run by a test, so it cannot rot.

Three things the batch decided that the plan had left open:

- **`agent_steps` stays, as the `Observer` port.** It is the only record of the SQL a local model
  wrote and the rows it got back. Writing it once at the end of a turn instead of five times inside
  one was considered and deferred: it is a behaviour change, not a move, and `schema.json` did not
  have to move for it.
- **`ai/mini.py` keeps its own loop.** Folding it in would mean giving the runtime a second ending —
  a terminal tool call rather than words — for one caller. `ReadToolSpec` stays in `ai/mini.py`; the
  runtime only ever needs the names, so `AgentDefinition.read_specs` is `Mapping[str, Any]`.
- **`agent_runtime.RunStatus` was written with six members**, and `safwa.models.AgentRunStatus` was
  left standing beside it with seven — 7.c deleted the duplicate. `claimed_at` keeps its one reader
  in `CueRuntime.can_speak` and its one writer in the claim.

Two destinations moved off the plan's map, each recorded above: `failure_reason` went to
`foundation/errors.py` rather than the runtime (the runtime keeps its own, because it cannot import
Safwa), and the context blocks went to `ai/messages.py` rather than `ai/advisor.py`.

| Exit criterion | Result |
|---|---|
| 1 `ai/service.py` does not exist | met — no import of it remains |
| 2 Rule F | 0 |
| 3 no application word in the package's public names | met, and tested |
| 4 the runtime starts on a scripted provider and an in-memory store | met — the example runs |
| 5 no general loop left in Safwa | met — `ai/advisor.py` calls the manager |
| 6 largest Safwa module ≤ 400 | **missed** — `ai/tools.py` is 543 |
| 7 DoD #3 6 → 5 | met |
| 8 Rule H 25 → 24 | met |
| 9 cycles 0 | met |
| 10 `prompt_prefix.json` byte-identical | met, checked after every step |
| 11 `schema.json` unchanged | met |
| 12 all 21 `AG` scenarios cited and green | met |
| 13 the two E2E suites added to, never traded | met |

Criterion 6 was written wrong. `telegram/callbacks.py` is 1278 lines and belongs to Phase 8, so no
batch here could ever have met "largest Safwa module ≤ 400"; what it should have said is that the
modules this batch *produces* stay under it. It does not fully hold even so: `ai/tools.py` is 543 —
everything Safwa does about a tool, in the file named for it. It stays under DoD #3's 600, and it is
not split for the sake of a number.

777 tests pass, 3 skipped. The module graph is 173 modules and 710 edges, cycles 0, 27 rule
violations. The largest Safwa module is now `telegram/callbacks.py`, which is Phase 8's.

### What Phase 7.c delivered

7.b moved the loop out of Safwa and left the reason it exists behind. A session could stop on a
person; nothing in the package could answer one. Resuming was a recipe the host assembled out of six
exported parts — claim, restore, replay the transcript, continue, build a receipt, walk it up the
chain — and leaving a session unfinished was a store method the port did not declare, so
`InMemorySessionStore` could not do it and the example could not resume. The border test could not
see any of that: no public name says a foreign word, and the package still could not be used by a
second host without rewriting its main obligation.

**One defect fell out of that on the way, and it was live.** `ProposalMaterializer.materialize`
called `advance_autoapprovals` from inside the session it was materializing. An automatic Save
claimed that same session, resumed it, and — for a routed subagent — delivered its receipt to a
caller still suspended in `_run_child`; when the child returned, the Advisor ran a second time.
Six provider calls for five scripted turns, and both runs stamped `completed` twice. The e2e harness
answers a sixth call itself, out of `calls`, so every test stayed green. `provider.total` is the
counter that would have said so, and `test_an_autoapproved_board_route_hands_back_its_receipt` now
asserts on it.

| Step | What changed |
|---|---|
| v7.1 | a screen is decided after the turn ends: `materialize` only reports waiting, `AgentManager._suspend` checkpoints, and the Advisor offers the screen to autoapproval from outside. `held_run_id` and `ProposalMaterializer`'s run store go with it |
| v7.2 | `InteractionRef`, `Resumption`, and `AgentManager.resume` / `interrupt`; `SessionStore.leave_interrupted`; the example and its test drive suspend and resume, and Safwa's e2e drives the interruption |
| v7.3 | `safwa.models.AgentRunStatus` deleted; recovery reads `RunStatus`, and a crashed session is `abandoned` rather than `interrupted` |

**The reference is the whole of the state machine that was built.** The plan asked for a frozen
`RunState` union, `RunAction`, `RunEffect` and a reducer. They were dropped after looking for their
reader: the loop reads a mutable `AgentSession` and the host reads a `TurnOutcome`, so eight state
classes and a reducer would be an abstraction with no caller. `InteractionRef` is the part that has
one — an opaque run id and a minted token, checked before a resume and cleared when it is taken. It
is what replaced the transaction that used to close the batch and claim the session together.

**`resolve_approval` is one decide and one resume.** Its `dialogue=` parameter is gone; nothing under
`src/` ever passed it, and a restored session carries its own. `AIAdvisor` no longer knows
`RunStatus`, `resumed_transcript`, `route_receipt` or `AgentManager.restore` — 335 lines to 287,
against `agent_runtime/manager.py` at 543.

**A resume that resumed nothing says nothing.** `resume` answers `None` for all three ways
that happens — the reference names a suspension the session has left, the session is already being
resumed, or its caller could not be taken because something else is resuming *it*. All three mean the
same thing to the interface: no words were generated, so the callback writes its own receipt, the
one a proposal outside a batch already gets. The words a subagent wrote in the third case were for
its caller, and only the session with no caller speaks to the chat — putting them in as an assistant
message is what the old fallback did, and it is the one thing that could have reached the dialogue
history looking like something the Advisor said.

**The recovery vocabulary means one thing per member.** `interrupted` is "left unfinished, and the
turn that routed here may come back for it", and after a restart there is no such turn — so a
restart records `abandoned` for both a running and a waiting session, which is what AG-SESSION-009
already said happens to them. `docs/AGENT_ARCH.md` said the opposite and was corrected.

**The review of the batch found three mechanisms that exist twice, and the cut finished.** §12.5
said the block-ordering helpers cross into the package and the block contents stay; v6.4 copied them
instead, so `ai/messages.py` carried its own `system_note`, `_append_user_message` and
`_cache_breakpoint` while the package's were exported and imported by nobody. `agent_steps` had two
writers — the `Observer` port for `route`, and four hand-rolled `AgentStep` inserts in `ai/tools.py`
— so `ToolAdapters` now takes the same trail the runtime does. `failure_reason` is deliberately
duplicated, because the runtime cannot import Safwa and Telegram must not import the runtime, but the
package no longer exports the copy nobody takes.

**`close_children` was the mechanism, and the property replaced it.** A session closed the sessions
it routed to only when it was the root of its turn, so a chain deeper than two would have leaked and
a root resumed by its own reference closed nothing at all. The flag is gone: every session closes its
own unfinished children when it finishes, and the chain ends from the bottom up however deep it is.
`_fail` is the same shape for the five places a session dies. **And `interrupt` on a session nothing
routed to now ends it** — its caller is the person, their words are its next request, and left
unfinished it was a record no turn could reach.

779 tests pass, 3 skipped. 173 modules, 712 edges, cycles 0, 27 rule violations, Rule F 0, DoD #3 4 —
`diff_lines` in `features/proposals/api.py` had no caller and took the file under 600 with it.
`prompt_prefix.json` and `schema.json` did not move.

### What the review of the phase changed

Three things the phase left, found by reading the delivered code against its own claims.

- **`query_safwa` existed twice.** `ToolAdapters.query` and `ai/mini.query_read_tool` were two
  implementations of one tool, with different error codes and different words for a refused SELECT,
  and three places declared it by hand. Worse, `ToolAdapters.run` answers its own names before it
  looks at a session's read tools, so a subagent that declared `query_safwa` was silently running the
  Advisor's copy. `ai/sql.py` now owns the whole read surface — the schema, `read_query`, and one
  wording for a refused read — `ToolAdapters` publishes the tool to every session it runs, and a
  feature declares only its own readers. `ai/tools.py` fell 533 to 476.
- **`close_unfinished_children` closed one level.** The claim that a chain ends however deep it goes
  held only because a routed subagent has no `route`. The store now walks the branch, so the depth of
  a chain is the store's business and not its caller's.
- **Three comments said the opposite of the code**: `parent_run_id` described as the session this one
  routed *to*, `_deliver_to_parent` documenting a `None` it never returns, and the rule about a
  subagent's first tool call sitting on an unrelated type alias.

783 tests pass, 3 skipped. The module graph, the cycles, the rule violations, `prompt_prefix.json`
and `schema.json` all held still.

### The seam with Phase 8

- **Phase 7 owns** the session, the loop, the budget, the claim, suspension on an opaque reference,
  and the store port.
- **Phase 8 owns** `as_state_flow()`, `TurnManager`, and everything the chat does with a run's state.
- A Phase 7 module that names a Telegram message, a `MessageKind` or `GenerationGuard` has crossed it.
- **One defect is Phase 8's**, because it lives in that seam: a review whose screen could not be drawn
  is never closed, and it holds the Cue gate shut until a restart. `ProposalMaterializer.materialize`
  opens the batch in `ProposalStore` — memory, not the transaction — before anything is rendered, so
  a `render_proposal` that raises leaves the batch open. `dismiss_prior_ui` finds an open review by
  its registered `APPROVAL` message, and the message that failed to send left no row, so the owner's
  next words close nothing. `reviews.busy` stays true, `CueRuntime.can_speak` stays false, and every
  Reminder and Sprint end goes unsaid. Nothing is lost — the `cues` rows are durable and wait — which
  is what makes it silent. The Cue path already guards itself: `CueRuntime.speak` calls
  `cancel_approval_for_proposal` in its own `except` for exactly this reason. The foreground path in
  `telegram/dialogue.py` does not, and that asymmetry is the whole bug.
- **`foundation/state_flow.py` is imported by no module under `src/`.** 230 lines waiting for the
  subscriber Phase 8 brings. Named here rather than deleted, because it is Phase 8's to use or drop.

### What the numbers should do

7.a moved `ai/service.py` from 2100 lines to 2193; 7.b deletes it. Every number the phase is held
to is in "Exit criteria, all of them checkable" above, because a target with an escape hatch is what
nearly let the largest file in the codebase survive the phase.

**`prompt_prefix.json` must not move.** Neither batch declares a prompt rewrite, 6.c already made the
one prompt change the phase depends on, and §12.5 makes byte-stability an explicit constraint on the
cut. `schema.json` moves only in 7.b, and only if `agent_steps` goes.

`tests/e2e/test_advisor_flow_e2e.py` and `tests/e2e/test_subagent_e2e.py` are the insurance for this
phase. They are added to, never traded for a faster unit test.

## Phase 7 notes: how an interruption is meant to work

Ruled on 2026-08-28, while packet two of Phase 6 was being read. Phase 6 does not build any of it;
these are the decisions Phase 7 starts from, so they are not derived a second time.

### What the owner's words are

Save, Discard and "no, call it Z" are three answers to one screen. Two of them resume the session
that opened it and one does not, and that is the whole defect.

- **Typed words resume the Advisor's turn.** They never start a new one, and they never reach a
  subagent first: only the Advisor can tell whether they correct the proposal or change the subject.
  `cancel_approval_for_proposal` stops setting the Advisor's run to `cancelled`.
- The order is unchanged and stays below the model: the Telegram handler rejects the pending
  proposals, closes the batch and freezes the screen into a report before anything is generated.
  What changes is only what happens to the two suspended sessions afterwards.
- The Advisor's pending `route` call is answered with what was proposed, what was rejected, what was
  already saved, and the owner's words.

### What each session's lifetime is

- **A subagent session lives while its work is unfinished.** Save finishes it: it plays out, hands
  back its receipt and closes, so a second `route` to the same subagent in the same turn gets a
  fresh session. An interruption does not finish it, so it stays and keeps its own plan.
- The turn that routed to it is the outer bound. When the Advisor's turn ends — answered or failed —
  it closes its unfinished children by `parent_run_id`. That is a direct close at a known moment,
  which is what lets `_close_lapsed_sessions` and its one-turn window be deleted rather than
  re-keyed. `recovery.py` keeps its idle sweep for a process that died mid-turn.

### `route` keeps carrying only a name

No `instruction` field. A small model restating a task drops half of it or invents the other half,
and the point of routing is that the subagent reads the conversation rather than a retelling.

The interruption result is therefore written into the subagent's own transcript **in full** — the
proposal it made, the refusal, and the owner's words. The words are also in the chat, but the
conversation a subagent reads is a token budget, so a long exchange can push them out. The
transcript is what makes it independent of that.

Its text has to say the owner refused **and wrote instead**. On "rejected" alone the subagent reads
its own transcript and proposes the same thing again.

### What this removes

The one-turn grace window, `_close_lapsed_sessions`, `_resume_suspended` on the interruption path,
the walk that cancels the callers, and the hint that would have told the Advisor a subagent's
proposal may have just been corrected. Five mechanisms, all compensating for one missing property:
the Advisor's turn could not be resumed by words.

It also ends a smaller wrong state. Today the kept subagent points at a cancelled parent until some
later turn adopts it; with the turn resumed, the parent is the same run throughout.

### The cost, accepted knowingly

A subject change inside one turn: the owner interrupts "rename X" with "forget it, create Y", and
the subagent resumes carrying the refused rename and its old plan while it creates Y. That history
is bounded by the turn — the next owner message ends the turn with an answer, and the session dies
with it.

### The assumption this rests on, and what to do if it breaks

Keeping the subagent's session is worth something only because its own plan is in its transcript.
That plan is there as far as the model wrote it into `content`, or started it as calls it already
made. A model that plans in a reasoning channel and emits a bare tool call with an empty `content`
leaves nothing behind, and then a kept session is no better than a fresh one.

Storing the reasoning is the wrong fix. There is nowhere to put it back: an OpenAI-compatible chat
completion does not take reasoning in the messages it is sent, so it would have to go back as
`content` — the model's private thinking returning as its own spoken turn. It is also long, and the
transcript is replayed on every resume, which a local model pays for.

Measure it first: after a suspension, whether `content` is empty on the assistant message that made
the call. If it routinely is, the fix is a line in the subagent prompt — say in one line what you
are about to do, then call the tool — which replays legally and costs a line.

### Facts checked while deciding, so they are not checked again

- `tool_count` and `repair_rounds` are in `state_json` and come back through `AgentSession.restore`,
  so `MAX_TOOL_CALLS` already bounds a turn however many times it is interrupted. No separate cap on
  interruptions is needed.
- A proposal screen is `MessageKind.APPROVAL`, which never becomes dialogue. A subagent cannot learn
  what it proposed from the conversation; only its own transcript holds that.
- `_claim_session` claims on `claimed_at IS NULL` and never reads `status`, so `cancelled` is a
  record rather than a guard.
- There is no reasoning channel anywhere. `reasoning_effort` is a request option; `CompletionTurn`
  carries `content`, `tool_calls` and `usage`, and nothing parses a separate reasoning field. A
  session keeps a plan only as far as the model wrote it into `content` or into calls it already
  made.

## Before starting Phase 8

Phase 8 in one line: the chat stops being Safwa's and becomes `telegram_llm`, the turn stops being
five loose fields and becomes named states, and the handlers stop living in one shared package and
go to the features that own them.

It is the largest batch of the migration by a wide margin, and it is the last one that moves
production code. `src/safwa/telegram/` is 6668 lines across seventeen modules; `history.py` is
another 653. Two of the four modules over 400 lines are in it, including the largest module left in
the codebase. `CALLBACK_ACTIONS` is one dictionary of about seventy entries, and DoD #1 counts 24
places outside `features/` that still fan out over entity names — nineteen of them are in
`telegram/`.

### The five things this phase owns

1. **`telegram_llm`** — the four invariants of §13.2: every outgoing message registered and marked,
   the chat as the canonical conversation, the single-use button, the temporary screen.
2. **`TurnManager`** — `GenerationGuard`'s five fields become the union of §9.3.
3. **The handler batch** — `FeatureModule` gains commands, callback actions and text-input flows, so
   a feature's screen moves into the feature. This is the thing §"Do not move the screens" told
   every Phase 4 batch to wait for.
4. **The last flat modules** — `domain.py`'s remaining 190 lines, `asr.py` behind its Protocol, and
   the two Rule G violations and one Rule L violation that go with them.
5. **The defect the Phase 7 review handed over** — a review whose screen could not be drawn stays
   open and holds the Cue gate shut until a restart. `SC-FAIL-005`.

### The scenario packages, written before any code moves

Four packets, eighteen new scenarios and one amendment, after two owner readings:

| Packet | Scenarios | Writes into |
|---|---|---|
| [docs/brd/telegram_history.md](brd/telegram_history.md) | 11, prefix `TG` | `tests/brd/telegram_history.feature`, new |
| [docs/brd/screens.md](brd/screens.md) | 4, prefix `SC` | `tests/brd/screens.feature`, beside `SC-LIVE-001` |
| [docs/brd/agents_turn.md](brd/agents_turn.md) | 2 new, and `AG-TURN-010` amended | `tests/brd/agents.feature` |
| [docs/brd/diary_day_read.md](brd/diary_day_read.md) | 1, `DI-READ-016` | `tests/brd/diary.feature` |

That is what the reading produced, not a target. Each packet carries the table of rules it
deliberately does not restate and where each of those already lives — the window's contents are
`TG`, but the moment a Summary is written stays `CO-SUMMARY-001`.

**Five of the nineteen are not green on current code**, and each says so in its packet:
`SC-SPLIT-004`, `SC-BUTTON-003`'s second block, `SC-FAIL-005`, `TG-NOTES-007` and the amended
`AG-TURN-010` with `AG-TURN-022` behind it. All five are changes the owner asked for, so 8.a is a
business batch that changes behaviour, not a technical one that preserves it.

### The two batches

The phase is two batches, split by where the code ends up: **8.a sends the chat out of Safwa**,
**8.b spreads what is left across the features that own it**. Each ends in a state that can be kept
forever — tests green, bot working, no old path beside a new one — and each is landed as a series of
steps that are green on their own, the way 7.b was landed in five.

Every behaviour change the owner asked for is in the batch that owns the code it changes, so
neither batch is a refactor carrying a surprise.

#### 8.a — the chat becomes a package

| Step | What |
|---|---|
| 1 | **Fix `SC-FAIL-005`**, before anything else. It is live: a review whose screen could not be drawn stays open, and every Reminder and Sprint end goes unsaid until a restart. `run_dialogue_turn` gains the failure handler `CueRuntime.speak` already has |
| 2 | Approve the packets and write them into `tests/brd/telegram_history.feature` (new, 11 `TG`), `screens.feature` (+4 `SC`), `diary.feature` (+`DI-READ-016`). Cite the tests that already hold a rule |
| 3 | **The notes stop feeding history.** Delete the floor, `_registered_message` and `MESSAGE_CORRELATION_SECONDS`; stop registering the owner's own messages. `TG-NOTES-007`, `TG-CURRENT-008` |
| 4 | **One send that splits.** Anything over the Telegram limit goes out in parts with no formatting left open across a cut; `send_summary` and `send_registered` both go through it, and `record_summary` names the last part. `SC-SPLIT-004` |
| 5 | **A button dies with the run that drew it.** `CALLBACK_TOKEN_TTL_HOURS` and `CallbackToken.expires_at` go; `recovery` clears every token; a press on a screen from before a restart replaces it with an out-of-date line. `SC-BUTTON-003` |
| 6 | **Extract `src/telegram_llm/`**: `_messaging.py` becomes the host, `history.py` the window, `_presentation.py`'s HTML half the safe send, `screens.py`'s lifecycle, `_core.py`'s models, and `asr.py`'s Protocol. `RECEIPT_MEANINGS` and `CITATION_TYPES` become configuration the host is handed; citations become `features/*/citations.py`; the ASR implementation becomes `adapters/asr.py` |
| 7 | **`examples/plain_chat_bot` on `telegram_llm` and `llm_gateway`** — about fifty lines, no Safwa import, run by a test the way the `agent_runtime` example already is |

Steps 3 to 5 are behaviour changes and go before the extraction on purpose: each one deletes code,
and moving less code is the cheapest way to move it. Step 5 is the only thing in the whole phase
that touches the schema.

#### 8.b — the handlers go to the features, and the turn gets a name

| Step | What |
|---|---|
| 1 | Amend `AG-TURN-010` in `tests/brd/agents.feature` and add `AG-TURN-022`, `AG-TURN-023`; delete the queue tests by name |
| 2 | **The queue goes and `TurnManager` arrives.** One message in the chat while an answer is written, with a tappable `/cancel`; anything else the owner sends is taken out of the chat and dropped. `GenerationGuard`'s five fields become the union of §9.3, minus `queued`, which this step deletes rather than models |
| 3 | Settle `foundation/state_flow.py`: a production reader, or delete it. 230 lines have been waiting for this since Phase 0, and Phase 7.c already refused to build a union with no reader |
| 4 | **`FeatureModule` grows** commands, callback actions and text-input flows — the mechanism §"Do not move the screens" told every Phase 4 batch to wait for |
| 5 | **The handlers move**, one feature group per step: Cards and Checks; Planning and the Sprint; Values, Tags, Requests, Reminders and Profile; Proposals. `callbacks.py`, `cards.py`, `commands.py`, `plan.py`, `checks.py`, `items.py`, `sprint.py`, `reminders.py`, `screens.py`, `text_input.py`, `proposals.py`, `dialogue.py`, `_messaging.py`, `_presentation.py` and `_core.py` dissolve; `telegram/__init__.py` is deleted |
| 6 | **The last flat modules**: `domain.py`'s remaining 190 lines to Cards, Checks, Planning and `foundation`; `main.py` to `bootstrap/`; Rule G's `adapters/asr.py` task; Rule L's `planning/background.py` import. `history.py` splits here too — the reader and the note table to `adapters/`, the citations to the features, which is what 8.a's criterion 3 was left waiting on |
| 7 | Regenerate the allowlist and update `CLAUDE.md` and `README.md` by deleting what stopped being true |

**Why this order.** The import cycle that cost Phase 4.a three deferred imports exists because a
feature's screen needs the shared UI core and the shared handlers need the screen back. Once that
core is a package which cannot import Safwa, the cycle is not possible to write — so 8.a has to
land before a single screen moves. `TurnManager` comes before the handlers for the same kind of
reason: handlers take and release the turn lease, and moving them against the old one means moving
them twice.

**Where `TurnManager` lives is still open**, and 8.b step 2 is where it has to be answered. §9.2
says `features/turn/`; the owner's reading is that a turn is the same kind of thing as a Cue, and
`src/safwa/cues/` is already a process living outside `features/` whose rules are written under the
identifiers of the features it serves. That shape gives `src/safwa/turn/` with rules staying
`AG-TURN-*` in `agents.feature`, and settles the conflict §9.2 creates with `tests/brd/README.md`.

**8.b is the larger of the two and the one to watch.** About 4500 lines of handlers and seventy
callback actions. Its steps are the safety: if step 5 cannot be finished, the batch rolls back
whole rather than leaving half a registry, which is two registries.

### Exit criteria, all of them checkable

Per batch, so a miss is attributable rather than deferred.

**8.a**

| # | Criterion |
|---|---|
| 1 | `src/telegram_llm/` exists; Rule F covers it, and no public name in it says `card`, `sprint`, `proposal`, `diary` or `safwa` |
| 2 | `examples/plain_chat_bot/` runs on `telegram_llm` and `llm_gateway` with no Safwa import, and a test runs it |
| 3 | `src/safwa/history.py` and `src/safwa/telegram/_messaging.py` do not exist |
| 4 | `_registered_message`, `MESSAGE_CORRELATION_SECONDS` and `CALLBACK_TOKEN_TTL_HOURS` do not exist |
| 5 | 16 scenarios cited and green — the 11 `TG`, the 4 `SC`, and `DI-READ-016` |
| 6 | no module produced by this batch is over 400 lines |
| 7 | `prompt_prefix.json` byte-identical; `schema.json` moves exactly once, for `callback_tokens` |

**8.b**

| # | Criterion |
|---|---|
| 8 | `src/safwa/telegram/` does not exist, and neither does `GenerationGuard` |
| 9 | no combination of turn state is legal that the type does not allow, and `QueuedMessage` does not exist |
| 10 | `foundation/state_flow.py` has a production reader, or it is deleted |
| 11 | DoD #1 24 → 1, and the one is `bootstrap/modules.py`; Rule H 24 → 0; Rule G 2 → 0; Rule L 1 → 0 |
| 12 | DoD #3 4 → 0, and neither `domain.py` nor `history.py` exists |
| 13 | `AG-TURN-010` amended, `AG-TURN-022` and `AG-TURN-023` green; the queue tests deleted by name |
| 14 | `prompt_prefix.json` byte-identical; `schema.json` unchanged |

**Both**

| # | Criterion |
|---|---|
| 15 | `tests/e2e/test_advisor_flow_e2e.py` and `tests/e2e/test_subagent_e2e.py` added to, never traded |
| 16 | the live Telegram suite passes: `uv run pytest tests\e2e\live --live-telegram -q` |

Criterion 6 is written as "produced by this batch" on purpose: that is the shape criterion 6 of
Phase 7 should have had, and it is the one it failed.

Criterion 10 is there because of what Phase 7.c found. A frozen `RunState` union and a reducer were
dropped after looking for their reader and finding none. `foundation/state_flow.py` is 230 lines
with no import anywhere under `src/`, and `TurnManager` is the reason it was written. If the turn's
state still has no second reader when 8.b step 2 is done, the honest move is to delete the flow and
keep the states.

### What the owner ruled on 2026-08-30, reading the first draft of the packets

1. **Split every outgoing message.** `SC-SPLIT-004` is settled: one send in `telegram_llm` splits
   anything over the Telegram limit and never leaves formatting open across a cut, and a Summary
   goes through it like everything else. A Summary is allowed 2000 tokens
   (`SUMMARY_TOKEN_CEILING = 2000`) and is sent whole today, so this is reachable rather than
   theoretical.
2. **Fix `SC-FAIL-005` first.** It is the first thing 8.a does, before the packets are written into
   `.feature` files. It silences every Reminder and every Sprint end until a restart.
3. **A button dies with the run of Safwa that drew it.** `CALLBACK_TOKEN_TTL_HOURS` and
   `CallbackToken.expires_at` go; a press on a screen from before a restart replaces that screen
   with an out-of-date line. `PR-STALE-013`'s exceptional lifetime for a proposal button stops
   being an exception. See [docs/brd/screens.md](brd/screens.md) §"What the two changes touch".
4. **Where `TurnManager` lives stays open**, and is not needed until 8.c. The owner's reading is
   that it is the same kind of thing as a Cue. That is a usable precedent: `src/safwa/cues/` is
   already a process that lives outside `features/` and whose rules are written under the
   identifiers of the features it serves. The same shape would put the turn in `src/safwa/turn/`
   with its rules staying `AG-TURN-*` in `agents.feature`, and would settle the conflict §9.2
   creates with `tests/brd/README.md`. Not decided.

8.a therefore changes behaviour rather than preserving it, and its gate is the approved packets.

### What the owner ruled on 2026-08-30, second reading

5. **The two id spaces stop mattering.** `_registered_message` and `MESSAGE_CORRELATION_SECONDS`
   are deleted in 8.a step 3. The Bot API and a Telethon user session number the same message differently,
   and about sixty lines of timestamp tie-breaking reconciled them for two jobs: reading an incoming
   message's kind off its note, which `TG-OWNER-003` already settles, and not counting the message
   being answered twice, which needs only the words Safwa already holds and the minute they were
   sent. `TG-CURRENT-008` keeps its wording; only the mechanism shrinks.
6. **Reading a named day is a Diary rule, not a window rule.** The second block of
   `TG-SUMMARY-006` moved out to `DI-READ-016`, in
   [docs/brd/diary_day_read.md](brd/diary_day_read.md). What that exposed — the Advisor has no way
   to reach a named day at all, and a Summary cannot be made into an index because it is free text
   with no dates of its own — is recorded in [docs/FUTURE_FEATURE.md](FUTURE_FEATURE.md) as a
   feature to design after the migration.
7. **Nothing queues behind a running answer.** While an answer is being written the chat carries
   **one** message saying so, with a tappable `/cancel`, removed when the answer arrives or when the
   owner cancels. Anything else the owner sends meanwhile is taken out of the chat and dropped — a
   recording is not even transcribed. This amends the approved `AG-TURN-010`, adds `AG-TURN-022`,
   and empties `Answering.queued` out of §9.3's union before it is built.
   [docs/brd/agents_turn.md](brd/agents_turn.md) lists what it deletes.
8. **Safwa reads the chat as far back as the budget allows.** The floor at the oldest note is
   deleted with the rest, so the note table stops being an input to reading the conversation
   entirely: `register_message` is no longer called for the owner's own messages, and the notes
   become a record of Safwa's outgoing messages and the screens among them. `TG-START-007` is
   rewritten as `TG-NOTES-007`.

Nothing is open except where `TurnManager` lives, which 8.b step 2 answers. 8.a may start.

### 8.a step 1 — a review that could not be drawn ends

`SC-FAIL-005` is fixed and green. A review is committed before its screen is drawn, and the owner's
path never closed one whose screen failed to send, so `ProposalStore.busy` stayed true and every
Reminder and Sprint end went unsaid until a restart. `CueRuntime.speak` guarded its own path and
was the only one that did.

The guard moved to where all three callers pass through — `render_ai_outcome` ends the review when
`render_proposal` raises, and re-raises so the caller still reports what failed. The duplicate in
`CueRuntime.speak` is gone, and `continue_agent_approval`, which never had one, is covered by the
same line. Two tests: an E2E over the real advisor asserting nothing is left open and nothing is
left claimed, and one over the owner's turn asserting they are told.

### 8.a step 2 — the scenarios that are already true

`tests/brd/telegram_history.feature` is new and carries ten `TG` scenarios; `screens.feature` gains
`SC-KEEP-002` beside the `SC-FAIL-005` step 1 wrote; `diary.feature` gains `DI-READ-016`. Sixteen
existing tests were cited rather than rewritten, and seven were written: four for `TG-KIND-002`,
`TG-OWNER-003`, `TG-SHAPE-010` and `TG-CURRENT-008`, two for `DI-READ-016`, one for `SC-KEEP-002`.

**Three scenarios are held back on purpose**, each until the step that makes it true — writing one
early would leave `test_every_approved_scenario_has_at_least_one_test` red between steps:
`TG-NOTES-007` at step 3, `SC-SPLIT-004` at step 4, `SC-BUTTON-003` at step 5. `AG-TURN-010`'s
amendment and `AG-TURN-022`/`AG-TURN-023` belong to 8.b step 2 for the same reason.

**One correction to the approved text.** `TG-OWNER-003`'s third branch said a command Safwa failed
to delete reads as a line the owner spoke. That is wrong in both directions: the code refuses any
owner text opening with `/`, and it is right to — a failed deletion would otherwise put `/today` in
the conversation as something the owner said. The branch now says so. The rest of the scenario is
unchanged: deletion is still what classifies ordinary text, and that is still the point of it.

### 8.a step 3 — the notes stop feeding history

`recent` no longer reads a note about anything the owner sent. Gone: the floor at the oldest note,
`_registered_message`'s sixty lines of timestamp correlation, `MESSAGE_CORRELATION_SECONDS`, and
the `register_message` call on the owner's own text. The registry read narrowed to
`direction == "out"`, matched on an event id Safwa minted itself. `TG-NOTES-007` is written in and
green.

The one thing that needed replacing is how the message being answered is recognised, and it splits
into two cases that are genuinely different: a transcript Safwa posted carries its own note, so the
event id is exact; the owner's own typed message carries none, so `_already_read` matches on the
words and the minute.

Three tests deleted rather than rewritten — `test_owner_text_older_than_every_registration_is_excluded`
asserted the floor, and `test_private_chat_correlates_telethon_and_bot_api_message_ids` and
`test_colliding_id_space_does_not_drop_the_newest_dialogue_message` existed only for the
correlation. One written, two renamed to what they now say.

**One consequence worth naming.** A value the owner typed into a field used to be recognised by its
note even if it survived in the chat; now nothing distinguishes it from ordinary text, so a failed
deletion puts it in the conversation. That is `TG-OWNER-003` as approved — deletion is the
classification — and it is why a command is the one thing that does not rest on it.

### 8.a step 4 — one send that splits

`send_prose` is the one way words go into the chat: it splits, and each part is marked and
registered on its own, so the window reads all of them and `dialogue` merges them back into the one
turn they were. `send_owner_turn` is now that plus the owner's name, `send_summary` is that plus
the cut place, and the Advisor's answer goes through it too. Only the first part may replace a
screen; the rest are new messages below it.

`split_telegram_text` prefers a line break, then a space, then the limit, moves the cut back out of
any tag or entity it lands in, closes whatever is still open at the cut, and opens it again at the
start of the next part.

**Two things this exposed.** A Summary carries its header on its first message only, so a split one
was recognised by nothing after that: `recent` now keys on the kind mark, which every part carries,
and gathers consecutive parts back into the one Summary. And the cut place `record_summary` stores
is the **last** part — the newest, which is where the backwards read meets it — or the window would
cut back into the middle of a Summary.

### 8.a step 5 — a button dies with the run that drew it

`CALLBACK_TOKEN_TTL_HOURS` and `CallbackToken.expires_at` are gone, `recovery` deletes every token
at start rather than the expired ones and the proposal ones, and `PROPOSAL_CALLBACK_ACTIONS` went
with the exception it existed for — which also takes `recovery.py`'s import of
`telegram/callbacks.py` with it.

The two branches of `SC-BUTTON-003` are told apart by whether the token is still there. Still there
and consumed is a second press: the alert, and the screen stays. Gone is a screen drawn by a run
that has ended: the screen is replaced with an out-of-date line, buttons and all, so nothing
answerable is left standing.

`test_proposal_save_ignores_button_age_while_the_process_is_running` was deleted rather than
rewritten — button age is the thing that stopped existing.

`schema.json` moved exactly once, for `callback_tokens`, which is the whole of the schema change
Phase 8 was allowed.

### 8.a step 6 — `telegram_llm`

The package a second bot could be built on, extracted in six green pieces.

| Piece | What it holds | Lines |
|---|---|---|
| `text.py` | `markdown_to_telegram_html`, `split_telegram_text` and the Telegram limit | 126 |
| `marking.py` | the invisible kind + event mark, over a host's own code table | 69 |
| `notes.py` | what a note is, and the four things the package asks of a store | 50 |
| `window.py` | the window, the layout into turns, receipts and citations | 415 |
| `host.py` | the send, the redraw, the screen lifecycle and the Toast | 357 |
| `voice.py` | `Transcriber`, and the clip and result it speaks in | 43 |

`safwa/history.py` went from 612 lines to 404 and `telegram/_messaging.py` from 568 to 298.
What is left in both is exactly what only Safwa can say: the Telethon reader, the
`telegram_messages` table behind `NoteStore`, the append-only mark codes, the
`ChatVocabulary`, its `MessageKind` values, its buttons, and what becomes of a review the
owner walked away from.

**The decisions worth recording.**

`MessageKind` stayed in Safwa. Moving it would have put `CARD_EDITOR` in a package whose
first exit criterion is that no public name in it says `card`. The package takes the kinds
as strings and is told what they mean, which is a smaller thing to be told than the
vocabulary itself.

`TelegramHistorySource` **is** a `ChatWindow` rather than holding one. Only a Telethon user
session can read a chat a bot is in, so the reader and the window are one object here; the
alternative was three delegating methods, which is the shape the rest of this migration has
been deleting.

`DialogueMessage` moved into the package, out of `safwa/ai/context.py`. The conversation is
what the package is about, and a turn of it is not an AI-context type.

`dismiss_prior_ui` was the entangled one and split in two. `ChatHost.leave_one_screen` walks
the screens and knows only that each has to stop being one; a `freeze` callback says what
each should become, and `None` means take it away. Safwa's half answers for exactly one
kind: a review is a question that was asked, so the chat has to keep saying it was asked and
how it ended, and that reaches the Advisor. Every other screen is only a state and goes.

`token_button` stayed in Safwa. A button is a row minted inside the transaction that draws
its screen, so a package that owned it would own the table too — and the package docstring
lost the line that claimed it.

The 85 `send_registered` call sites did not move. `_messaging.py` keeps every name it
published and each is now three lines over `ChatHost`, converting `MessageKind` to the
string the package takes — the same adapter shape `history.py` kept for `mark_message`.

`safwa/asr.py` became `safwa/adapters/asr.py`: with the Protocol in the package, what is
left is the two engines behind it.

**Not done in this step.** Citations are still `history.py`'s `CITATION_TYPES` rather than
each feature's own. The move needs the treatment `SqlView` got — a catalogue the composition
root assembles and hands out — because `CITATION_PATTERN` is compiled at import and
`screens.py` reads it there. That is `FeatureModule` growth, which batch 8.b already opens.

### 8.a step 7 — the example that proves the border

`examples/plain_chat_bot/bot.py`: a bot that only talks, in 142 lines, importing
`telegram_llm`, `llm_gateway` and aiogram and nothing else. `tests/test_telegram_llm.py`
runs it against a `ScriptedProvider` and a fake Telegram, and asserts the thing the package
exists for — the bot keeps no conversation of its own, so the second turn's request is the
first turn read back out of the chat.

The Phase 7 example was already at `examples/plain_chat_bot/` and is a note keeper, not a
chat bot, so it moved to `examples/note_keeper/` and both names are now true.

**What the example had to say out loud.** The window asks an application for two things: a
place for notes about the bot's own messages, and a way to read the chat back. The example
answers both with one class of forty lines — a dict, and the list of messages an aiogram bot
has already seen. Safwa answers the same two with a SQLite table and a Telethon user
session. Neither shape is in the package, which is what reusable had to mean here.
`ChatVocabulary` takes the whole of what a host must say about its kinds, and the example
fills it in three lines; its mark codes are 1, 2, 3, because nothing in the package knows
Safwa's are append-only from 3 to 15.

`test_the_vocabulary_of_the_package_belongs_to_no_application` reads every public name in
every module of the package and rejects Safwa's nouns. It matches whole words rather than
substrings: `discard_toast` is not about a Card.

Exit criterion 6 then forced a seam that was worth having. `window.py` was 415 lines because
it also held `restore_citations` and `split_receipts` — a citation that came back as a link,
and a line the interface added rather than the model. Both are pure transformations of a
message's text, which is what `text.py` already was in the other direction. `text.py` is 191
lines and `window.py` 353, and the package's largest module is now `host.py` at 357.

### 8.a — exit criteria

| # | Criterion | |
|---|---|---|
| 1 | `src/telegram_llm/` exists, Rule F covers it, no public name says Safwa's nouns | met |
| 2 | `examples/plain_chat_bot/` runs on the two packages with no Safwa import, and a test runs it | met |
| 3 | `src/safwa/history.py` and `src/safwa/telegram/_messaging.py` do not exist | **missed** |
| 4 | `_registered_message`, `MESSAGE_CORRELATION_SECONDS`, `CALLBACK_TOKEN_TTL_HOURS` do not exist | met |
| 5 | 16 scenarios cited and green — 11 `TG`, 4 `SC`, `DI-READ-016` | met |
| 6 | no module produced by this batch is over 400 lines | met |
| 7 | `prompt_prefix.json` byte-identical, `schema.json` moved once for `callback_tokens` | met |

**Criterion 3 is missed, and deliberately.** Both modules shrank to adapters — 612 lines to
404 and 568 to 298 — and neither can be deleted by this batch.

`_messaging.py` is 85 `send_registered` call sites and forty more across the other names, in
the handler modules that 8.b step 5 dissolves. Deleting it now means editing every one of
those call sites, and 8.b then edits them again when the handler moves to its feature. That
is moving the same lines twice, which is the argument this phase's own ordering rests on.

`history.py` cannot go to `adapters/` whole, because it is two things: the Telethon reader,
the note table and the mark codes, which are an adapter — and `CITATION_TYPES`,
`CITATION_PATTERN` and `conversation_block`, which are Safwa's semantics and are the
deferred citations move. Splitting it is that move, not this batch.

Both are carried into 8.b as named work rather than left as a claim that the batch is
finished.

## What Phase 8.a delivered

The batch it was: three behaviour changes that delete code, then the extraction, then the
example that says whether the border was drawn in the right place. Seven steps, seven green
suites.

| Step | Planned | Landed |
|---|---|---|
| 1 | Fix `SC-FAIL-005`: a review whose screen could not be drawn stays open | `render_ai_outcome` ends the review and re-raises. One choke point for all three callers, so `CueRuntime.speak`'s duplicate of it went |
| 2 | Approve the packets; 11 `TG`, 4 `SC`, `DI-READ-016` | written, cited, green |
| 3 | The notes stop feeding history | the floor, `_registered_message` and `MESSAGE_CORRELATION_SECONDS` deleted; the owner's own messages are no longer registered |
| 4 | One send that splits | `send_prose`, and `send_owner_turn`, `send_summary` and the answer path all go through it. Found and fixed a read-back defect: only the first part of a split Summary carries the heading, so later parts were being dropped |
| 5 | A button dies with the run that drew it | `CALLBACK_TOKEN_TTL_HOURS` and `CallbackToken.expires_at` gone; `recovery` clears every token; a press on a screen from before a restart replaces it |
| 6 | Extract `src/telegram_llm/` | seven modules, 1125 lines: `text`, `marking`, `notes`, `window`, `host`, `voice` and the `__init__`. `history.py` 612 → 404, `_messaging.py` 568 → 298, `asr.py` → `adapters/asr.py` |
| 7 | The example that proves the border | `examples/plain_chat_bot/bot.py`, 142 lines, two packages and aiogram; `tests/test_telegram_llm.py` runs it |

**Numbers at the end of the batch.** 812 passed, 3 skipped; `ruff check .` clean; 181 modules;
Rule F 0, Rule G 2, Rule H 24, Rule L 1; DoD #1 24. `prompt_prefix.json` byte-identical and
`schema.json` moved exactly once, for `callback_tokens`.

**What the batch changed that was not on the list.** Three, each small and each recorded with
its step. The Summary read-back defect in step 4. `MessageKind` staying in Safwa rather than
moving to the package, because `CARD_EDITOR` would have broken the package's first exit
criterion and its stored value cannot be renamed under live rows. And `examples/plain_chat_bot/`
was already taken by the Phase 7 note keeper, which moved to `examples/note_keeper/`.

### What 8.a did not finish, and where it goes

Exit criterion 3 — `history.py` and `_messaging.py` do not exist — is the one miss, and both
halves are carried into 8.b by name rather than left as a claim.

`_messaging.py` is 298 lines of adapter over `ChatHost`, called from 185 places in the handler
modules that 8.b step 5 dissolves. Deleting it now edits every one of those call sites, and 8.b
edits them again when the handler moves to its feature. 8.b criterion 8 already covers it: the
whole of `src/safwa/telegram/` goes.

`history.py` is 404 lines of two different things. The Telethon reader, the `telegram_messages`
table behind `NoteStore` and the append-only mark codes are an adapter and belong beside
`adapters/asr.py`. `CITATION_TYPES`, `CITATION_PATTERN` and `conversation_block` are Safwa's own
semantics, and moving them is the deferred half of step 6: citations become each feature's, which
needs the treatment `SqlView` already got — a catalogue the composition root assembles and hands
out — because `CITATION_PATTERN` is compiled at import and `screens.py` reads it there. That is
`FeatureModule` growth, which 8.b step 4 opens anyway. Both land in 8.b step 6, and criterion 12
now names `history.py`.

Nothing else was deferred. The rest of the phase's list is 8.b as written.

## The 8.a review

Read after the batch was green, against the exit criteria and the four packets. Nine things,
landed together: 814 passed, 3 skipped; `ruff check .` clean; 181 modules; Rule F 0, G 2, H 24,
L 1; DoD #1 24; `prompt_prefix.json` and `schema.json` unmoved.

**Two defects, each with a test that fails without its fix.**

A run of Summary parts was ended only by a message that is conversation, so two Summaries with
nothing but a screen or a receipt between them were read back as one — against `TG-SUMMARY-006`,
"an older Summary is never read". A run is now ended by any message at all.

A caller's own delivery identifier went on the **first** part of a split send. `CueRuntime.speak`
reads that identifier back to mean the owner has the words, so a send that failed halfway left the
Cue row deleted and the answer truncated for good. It goes on the **last** part now, which is the
same reasoning `record_summary` already followed, and a half-finished Cue is retried whole.

**One thing that only accumulated.** `ChatHost.remove_screen` kept the note when Telegram refused
the delete and only the buttons came off. A message with nothing left to press is not a screen, and
one kept was walked and stripped again on every later event.

**The border was one import short.** `telegram_llm/text.py` imported `telethon.helpers` for two
UTF-16 conversions, so the package a second aiogram bot was supposed to be built on pulled in the
user-session library, and the example's "no import but the two packages and aiogram" was false at
run time. Both conversions are now six lines in `text.py`, and
`test_no_module_in_the_package_imports_the_application` rejects `telethon` and `sqlalchemy` beside
`safwa` — where the notes are kept and how the chat is read back are the two ports, so neither
answer may be named inside the package.

**Four leftovers deleted.** `history.py` re-exported `split_receipts`, which nothing outside the
package used, and `restore_citations`, which only a test reached through it; `CONVERSATION_TAGS`
was public and module-local; `read_message_mark` existed only for tests, which now read `MARKS`.
`_messaging.clear_message_markup` had no caller at all. And `ai/context.py` had become a door for
`telegram_llm`'s `DialogueMessage`, with thirteen importers going through it; they import it from
the package now and the door is gone.

**One string was written in two places.** `SUMMARY_HEADER` is what the window strips back off a
Summary, and `persona.py` wrote the same characters by hand. It is
`features/continuity/model.py`'s now, written and stripped from there — the shape
`RECEIPT_MEANINGS` already had.

**Comments.** The `_messaging.py` adapters restated `ChatHost`'s docstrings almost word for word,
which puts the same rule in two places and neither says it is the copy. What is left in each is
Safwa's own half: the `MessageKind` it chooses, and the cut place a Summary stores. Two comments
promising a move "in Phase 8" now say 8.b.

### What the review left for 8.b

**Criterion 11 cannot be met as written.** Rule G is 2, and one of them is now
`telegram_llm/host.py` — the Toast timer, which moved there with the rest of `_messaging.py`.
`LIFECYCLE_MODULES` names only `safwa/main.py` and `safwa/bootstrap/main.py`, so a package that
expires its own Toast violates the rule wherever it lives. Either 8.b step 6 gives the timer back
to the host, or criterion 11 reads "Rule G 2 → 1, and the one is the package's Toast". Owner's call.

**`_edit_lock` and `_toasts` are module globals in `host.py`**, so one process has one Toast slot
whatever it is running. They cannot become `ChatHost` fields while `_messaging._host()` builds a
fresh `ChatHost` on each of its 185 calls; the host gets a home when `_messaging.py` dissolves in
8.b step 5, and the globals move with it.

**Criterion 16 is unverified.** The live Telegram suite needs a second BotFather bot and the
`SAFWA_QA_*` variables, so it was not run here.

## What Phase 8.b delivered

The handlers went to the features, the turn got a name, and the flat modules are empty.

```
                 8.b start                        8.b end
modules          181                              212
Rule F/G/H/L     0 / 2 / 24 / 1                   0 / 0 / 1 / 0
DoD #1 / #3      24 / 3                           1 / 1
tests            814 passed, 3 skipped            795 passed, 3 skipped
src/safwa/       telegram/ 6,329 lines, 16 files  telegram/ does not exist
                 history.py 392, domain.py 190,   neither exists; main.py is
                 main.py 250                      bootstrap/main.py
```

Fewer tests at the end than at the start: the queue tests were deleted by name in step 1, because
the behaviour they asserted was amended out of `AG-TURN-010`.

| Step | What it moved |
|---|---|
| 1–2 | `AG-TURN-010` amended, `022` and `023` written; the queue deleted and `TurnManager` arrived in `src/safwa/turn/` |
| 3 | `foundation/state_flow.py` deleted, the states kept |
| 4a–4d | `FeatureModule` grew `screens`, `commands`, `callback_actions`, `text_inputs`; four central registries gone |
| 5a | `src/safwa/shell/` — the router, the container, the middleware, the menu, the token dispatch |
| 5b–5e | Cards, Checks, Planning, Values, Tags, Requests, Reminders, Profile, Proposals; `src/safwa/telegram/` deleted |
| 6 | `domain.py` and `history.py` deleted, `main.py` became `bootstrap/main.py`, Rule G to 0, `CARD_EDITOR` became `EDITOR` |
| 7 | the allowlist, these documents, and the last test monolith |

### The six things reading the code changed, and the decisions taken on them

1. **DoD #3 cannot reach 0.** `features/cards/use_cases.py` (816) is a feature module in its right
   place, and its size is the guarantee its docstring states: every writer of an Action's stage is
   in one file. Criterion 12 reads `3 → 1` with that as the named reason.
2. **Rule H reached outside `telegram/`**, so the catalogue of openable and citable items is a
   `FeatureModule` contribution, `screens`, and not only a UI mechanism.
3. **That catalogue would have moved `prompt_prefix.json`.** `OpenInput.item_type`'s `Literal` stays
   hand-written in `ai/contracts.py`: a tool's enum is prompt text, and prompt text is written where
   the model reads it. It is Rule H's last entry, on purpose.
4. **Rule G's second entry was inside `telegram_llm`.** Neither module starts a task now:
   `ChatHost` takes a `Spawn`, and `bootstrap/main.py` cancels every Toast timer it started. A timer
   the package started outlived shutdown, because nothing outside knew it existed.
5. **Step 5 had no home for the shell.** `src/safwa/shell/` is it — Safwa's Telegram application,
   and the only module besides `telegram_llm` that a feature's adapter may import.
6. **8.b had no ceiling on what it produced**, so criterion 17 was added: 400 lines.

### What was decided while it ran

**A `back` payload names the action that draws the screen.** `{"action": "card_view", "id": 12, …}`,
dispatched by `shell.go_back` through the same table every inline button goes through. It replaced a
branch over six screen kinds that reached Cards, Planning and Saved Requests from one function — a
registry of screens, in a codebase that already has one.

**`FeatureModule` grew a sixth contribution, `start_links`.** A `/start` payload a feature answers
itself, tried in `MODULES` order; a payload none of them claims opens the item it cites. Taken with
one user, Planning, because the alternative is the shell knowing what a Sprint plan is.

**Rule E became a layer.** It used to list who was allowed which door, and the list had grown a
carve-out per hard case. It now says one thing: a feature is three layers deep — `model.py` and
`api.py` are what a thing is called, `use_cases.py` is what may be done to it, everything else is
assembly — and **a module opens a door at its own layer or below, never above, in any feature
including its own.** The edge it forbids is `api` opening `use_cases`: a door that imports what is
built on top of it has the whole feature behind it, and two such doors facing each other is an
import cycle.

Two doors were carrying operations and had to give them up. `checks/api.py` published eleven names
out of its own use cases and defined two more; `reminders/api.py` published three. Both now carry
the vocabulary and the reads that need no operation, and their callers — all at the operations layer
or above — ask `use_cases.py` directly. The screen that had reached `saved_requests.use_cases` got a
door of its own on the way, `features/saved_requests/api.py`.

Rule E is 0 under the new statement, with nothing exempted.

**Rules B and L were removed rather than reworded.** Rule L watched for a feature importing
`safwa.domain`, which step 6 deleted; a rule whose subject cannot exist is not a guard. Rule B said
adapters do not open a transaction, and scanned by file name — `telegram.py` and `agent.py` — so it
only ever saw the two single-file adapters and the agent contracts. Package adapters commit in some
forty places, by design: a screen owns its unit of work. The rule passed because of what it did not
scan, and its one real check, that an agent contract never commits, is Rule K's and unchanged.

**Rule G stopped naming files.** It exempted a list of two paths and a file called `manager.py`,
which had stopped starting tasks a phase earlier. It now says that whoever starts a background task
also cancels one, which is the property it was always after: owning the lifetime is the permission.
**Rule H stopped naming a path** and finds the registry by what it declares, so its exemption
follows `MODULES` if that ever moves.

**`bootstrap_workspace` went to `bootstrap/main.py`, not to `foundation/`**: one of the two rows it
seeds is `features/profile`'s model, and foundation is what every feature calls, not the other way
round.

**One regression was found and closed.** Since step 5c, the 🏃 Sprint screen called
`planning/use_cases.finish_sprint`, which does not archive, while every test called the `domain.py`
wrapper, which does — so half of `PL-END-014` was broken and untestable at once. There is one
`finish_sprint` now, in Planning, and it sweeps.

### Exit criteria

Every criterion is met except one, which could not be run here.

| # | Criterion | Verdict |
|---|---|---|
| 8 | `src/safwa/telegram/` and `GenerationGuard` do not exist | met |
| 9 | no illegal combination of turn state is representable; `QueuedMessage` is gone | met |
| 10 | `foundation/state_flow.py` deleted | met |
| 11 | Rule H 24 → 1 (the `open` tool's enum), G 2 → 0, L 1 → 0, DoD #1 24 → 1 | met |
| 12 | DoD #3 3 → 1 (`features/cards/use_cases.py`); neither `domain.py` nor `history.py` exists | met |
| 13 | `AG-TURN-010` amended, `022` and `023` green, the queue tests deleted by name | met |
| 14 | `prompt_prefix.json` byte-identical, `schema.json` unchanged | met |
| 15 | the two E2E suites added to, never traded | met |
| 16 | the live Telegram suite passes | **not run**: it needs a second BotFather bot and the `SAFWA_QA_*` variables |
| 17 | no module produced by this batch is over 400 lines | met for source; the largest is `features/values/telegram/screens.py` at 398. Two test modules are over it — `tests/test_cards_ui.py` (732) and `tests/test_planning_ui.py` (428) — and are named rather than split |
| 18 | `tests/test_telegram_item_ui.py` does not exist | met: it is `test_shell_wiring.py`, `test_turn_ui.py`, `test_interruptions_ui.py`, `test_citations_ui.py`, `test_proposals_ui.py`, `test_voice_ui.py` and `test_chat_ui.py` |
| 19 | `CARD_EDITOR` does not exist, and `MessageKind` is still Safwa's | met |

**Criterion 16 is carried into Phase 9**, as it was carried out of 8.a. So is one stale name:
`tests/test_domain.py` is named after a module that no longer exists, and its contents are half
Cards' tree and half the Sprint's scope, so it has no one owner to be renamed to.
`tests/test_history.py` did have one and is `tests/test_chat_history.py`.

**One stored value is stale.** `MessageKind.CARD_EDITOR` became `EDITOR`, and the mark code is
unchanged, so every message already in the chat reads back as the kind it was sent under. Rows
written before the rename still say `card_editor` in `telegram_messages.kind`; all of them are
screens and typed values, none of which is dialogue. Rebuilding the database, or
`UPDATE telegram_messages SET kind = 'editor' WHERE kind = 'card_editor'`, makes them match.

### Phase 8 post-review audit

The `v9.3` and `v9.4` review commits tightened the architecture checks and removed a second,
implicit menu registry, dead compatibility surfaces, stale constants, and comments that merely
repeated field names. The resulting test-count change is intentional: `v9.3` removed two obsolete
parametrized architecture cases, `v9.4` added the menu-order regression, and this audit added the
failed-freeze regression. The current suite is **794 passed, 3 skipped**.

The post-review audit found and closed three defects around the Phase 8 boundaries:

1. A screen whose final freeze edit failed had its buttons cleared but stayed registered as a live
   screen. `ChatHost` now forgets that note, so the next event cannot walk and delete a screen whose
   review has already ended.
2. Shutdown cancelled background and Toast tasks without awaiting the complete cancelled set.
   Cancellation could therefore overlap SQLAlchemy disposal and leave `aiosqlite` worker-thread
   exceptions for the next test. Shutdown now cancels first, then awaits every owned task together;
   pytest treats any future `PytestUnhandledThreadExceptionWarning` as an error.
3. The opt-in live Telegram fixture still patched the pre-Phase-8 scheduler functions and used the
   old history-factory signature. It now disables `BACKGROUND_TASKS` at the composition root and
   supplies the citation types expected by `SharedHistoryFactory`.

Redundant comments in the shell container, chat-state declaration, and bootstrap assembly were
removed. Comments that explain a business, ownership, or concurrency invariant remain.

The architecture report is unchanged at the intended end state: 212 modules, 837 edges, no import
cycles; Rules E/F/G/L and DoD #2/#13 are 0; Rule H and DoD #1 are 1 for the named `OpenInput`
exception; DoD #3 is 1 for `features/cards/use_cases.py`. Criterion 16 remains the only external
gate not executed in this environment because the `SAFWA_QA_TELEGRAM_*` credentials and second bot
are not configured. The live fixture now collects against the current composition root, but a real
Telegram run is still required before that criterion can be marked met.
