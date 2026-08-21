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
| 4 | Leaf business batches | business | not started |
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
