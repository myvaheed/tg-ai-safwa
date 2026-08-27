# How a feature plugs in

A Safwa feature declares what it contributes once, in its own `module.py`, and
[`bootstrap/modules.py`](../src/safwa/bootstrap/modules.py) lists it once. Nothing else in the
codebase names a feature: `tests/test_architecture.py` Rule H fails on any module outside
`features/` that spells an entity out.

## The manifest

[`bootstrap/module_manifest.py`](../src/safwa/bootstrap/module_manifest.py) holds the wiring DTOs.
It is the outermost layer, so it may know aiogram and SQLAlchemy; nothing that expresses a business
rule imports it.

| Field | What it contributes |
|---|---|
| `agents` | an `AgentSpec` — the subagent `route(name)` reaches, and the line the Advisor's prompt carries |
| `proposals` | a `ProposalContribution` per entity: handler, mutation tool, presenter |
| `mutation_tools` | a mutation tool whose change lands on an entity another feature owns (`remove`) |
| `views` | the `ai_*` views this feature publishes |
| `recover` | one hook `recover_startup` runs before the run machinery is reconciled |
| `background` | tasks the polling loop starts and cancels |

A capability does not get a field here by default. It first gets its own mechanism, and only a
capability several features plug into earns a contribution. Routers and bot commands are not in the
manifest yet: they join it in the phase that moves the Telegram handlers into their features.

## The three proposal responsibilities

One registration, three layers, bound only in the feature's `module.py`:

- **`ProposalHandler`** (`features/<f>/proposal.py`) — `prepare` checks the change against live
  data and writes nothing; `apply` calls the same feature operations the manual UI calls. Loading the
  entity, `archived_at`, the closed repeat and `target_not_found` live here, not in generic code.
- **`MutationToolSpec`** (`features/<f>/agent.py`) — the Pydantic input model, the one-line
  description, and the conversion into a neutral `AgentChange`.
- **`ProposalPresenter`** (`features/<f>/telegram.py`) — the receipt lines and the review screen.
  `screen()` may return `None`, which falls back to the generic change list.

Rule K keeps them apart: `proposal.py` carries no user-facing wording and no aiogram, and `agent.py`
neither commits nor calls the domain.

What stays generic is the orchestration: the workspace and its revision, the batch and proposal
rows, the optimistic lock, and the ordered walk over the stored changes
([`ChangePreparer`](../src/safwa/ai/prepare.py), `ProposalService`).

## Derived registries

`bootstrap/modules.py` builds these from `MODULES` at import time, and fails fast on a duplicate
entity, tool or view:

- `AI_VIEWS` and `ALLOWED_VIEWS` — one catalogue behind both `CREATE VIEW` and the read allowlist.
  It is passed as data to whoever validates against it, so `ai/sql.py` stays a leaf.
- `PROPOSALS` — the `ProposalRegistry` the advisor, the proposal service and the review screen read.
- `SYSTEM_PROMPT` — the template in `ai/context.py` with the routing rules generated from the
  roster. `MODULES` is a constant of import time, so the cacheable prompt prefix stays byte-stable.
- `RECOVERY_HOOKS` and `BACKGROUND_TASKS` — in `MODULES` order.

## Adding an entity the model may change

```text
safwa/features/<feature>/
  __init__.py   # empty: importing one leaf must not drag in the manifest
  module.py     # MODULE = FeatureModule(...)
  model.py      # feature-owned ORM entities, their field enums and value constants
  use_cases.py  # business operations shared by every adapter
  api.py        # what another feature may call; Rule E allows no other door
  references.py # ReferenceSpec per named relationship the feature carries
  views.py      # SqlView per ai_* view
  agent.py      # MutationToolSpec, and an AgentSpec if it owns a subagent
  proposal.py   # ProposalHandler
  telegram.py   # ProposalPresenter
```

These names are the whole vocabulary. A feature that wants a file outside this list is saying its
contents belong to a role the list does not have yet, which is a question for the batch, not a new
word.

`api.py` **defines** what it publishes. It exists only when another feature actually calls in, and
it hands over the answer rather than the row: `scheduled_memory_time(session)`, not `UserProfile`.
A module that only re-exports is counted as a leftover path by Definition of Done #13.

`references.py` declares one [`ReferenceSpec`](../src/safwa/foundation/references.py) per named
relationship — a Card carries Values, Tags and Checks, a Check carries Values — so a payload key,
its lookup, its junction row and the toggle that writes it are one record. It is separate from
`api.py` because each spec names a toggle from `use_cases.py`, and `api.py` cannot import those:
Planning reads through the Cards door while the Card use cases read through Planning's.

## Who owns the transaction

One rule, because the alternative is a reentrancy question with no good answer:

- A **use case takes an `AsyncSession` and never commits.** It is composable, which is what lets
  `ProposalHandler.apply`, a recovery hook and a Telegram handler all run the same operation.
- The **caller owns the transaction.** `Database.transaction()` is that boundary, and opening one
  inside another raises rather than silently joining — a joined block has no savepoint, so a caught
  inner failure would ride along into the outer commit.
- A use case that has to know the time **takes a `Clock`.** The composition root binds
  `SystemClock()`; near midnight and across a timezone change the result is then reproducible.

This amends plan §5, which had the use case open its own transaction. It could not: every write
path in Safwa already reaches the feature from inside a session someone else opened.

Then one line in `MODULES`. That is the whole edit in central code: the mutation tool and its
schema, the review screen, the view and its allowlist entry, the routing line, the recovery hook and
the background task all follow from the declaration.

The Diary is the pilot for the complete shape. Its proposal handler and Telegram adapter both call
`features/diary/use_cases.py`; its agent input model and presentation constants stay inside the
feature. Reads are deliberately asymmetric: the Advisor reads and opens `ai_diary` directly, while
writes route to the Diary subagent and remain proposals until Save.

## Why `__init__.py` is empty

`module.py` reaches aiogram, the domain and the Telegram adapters. `views.py` and `api.py` are
leaves that low-level modules import. Keeping the package `__init__` empty is what stops importing a
leaf from executing the manifest, which is how the import graph stays acyclic.
