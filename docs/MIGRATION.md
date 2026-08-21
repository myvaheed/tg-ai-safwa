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
| 2 | `FeatureModule` and proposal capabilities | technical | not started |
| 3 | Pilot: Diary | business | not started |
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
