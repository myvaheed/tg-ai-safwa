# How a feature plugs in

A Safwa feature declares what it contributes once, in its own `module.py`, and
[`bootstrap/modules.py`](../src/safwa/bootstrap/modules.py) lists it once. Nothing else in the
codebase names a feature: `tests/test_architecture.py` Rule H fails on any module outside
`features/` that spells an entity out.

## The manifest

[`shell/manifest.py`](../src/safwa/shell/manifest.py) holds the wiring DTOs.
It is the outermost layer, so it may know aiogram and SQLAlchemy; nothing that expresses a business
rule imports it.

| Field | What it contributes |
|---|---|
| `agents` | an `AgentSpec` — the subagent `route(name)` reaches, and the line the Advisor's prompt carries |
| `proposals` | a `ProposalContribution` per entity: handler, mutation tool, presenter |
| `mutation_tools` | a mutation tool whose change lands on an entity another feature owns (`remove`) |
| `views` | the `ai_*` views this feature publishes |
| `screens` | a `ScreenSpec` per item type the owner can be taken to, and how it reads when cited |
| `commands` | a `ScreenCommand` per screen the owner opens by name: a slash command, a menu button, or both |
| `callback_actions` | the inline-button actions this feature's screens draw |
| `text_inputs` | a `TextInputFlow` per editor field the owner types a value into |
| `start_links` | a `StartLink` — a `/start <payload>` this feature answers instead of it opening a cited item |
| `recover` | one hook `recover_startup` runs before the run machinery is reconciled |
| `background` | tasks the polling loop starts and cancels |

A capability does not get a field here by default. It first gets its own mechanism, and only a
capability several features plug into earns a contribution.

## The three proposal responsibilities

One registration, three layers, bound only in the feature's `module.py`:

- **`ProposalHandler`** (`features/<f>/proposal.py`) — `prepare` checks the change against live
  data and writes nothing; `apply` calls the same feature operations the manual UI calls. Loading the
  entity, `archived_at`, the closed repeat and `target_not_found` live here, not in generic code.
- **`MutationToolSpec`** (`features/<f>/agent.py`) — the Pydantic input model, the one-line
  description, and the conversion into a neutral `AgentChange`.
- **`ProposalPresenter`** (the feature's Telegram adapter) — the receipt lines and the review
  screen. `screen()` may return `None`, which falls back to the generic change list.

Rule K keeps them apart: `proposal.py` carries no user-facing wording and no aiogram, and `agent.py`
neither commits nor calls the domain.

What stays generic is the orchestration: the workspace and its revision, the batch and proposal
rows, the optimistic lock, and the ordered walk over the stored changes
([`ChangePreparer`](../src/safwa/features/proposals/prepare.py),
[features/proposals/use_cases.py](../src/safwa/features/proposals/use_cases.py)).

## Derived registries

`bootstrap/modules.py` builds these from `MODULES` at import time, and fails fast on a duplicate
entity, tool or view:

- `AI_VIEWS` and `ALLOWED_VIEWS` — one catalogue behind both `CREATE VIEW` and the read allowlist.
  It is passed as data to whoever validates against it, so `ai/sql.py` stays a leaf.
- `PROPOSALS` — the `ProposalRegistry` the advisor, the proposal use cases and the review screen
  read.
- `SYSTEM_PROMPT` — the template in `features/advisor/agent.py` with the routing rules generated
  from the roster. `MODULES` is a constant of import time, so the cacheable prompt prefix stays
  byte-stable.
- `RECOVERY_HOOKS` and `BACKGROUND_TASKS` — in `MODULES` order.

## Adding an entity the model may change

```text
safwa/features/<feature>/
  __init__.py   # empty: importing one leaf must not drag in the manifest
  module.py     # MODULE = FeatureModule(...)
  model.py      # feature-owned ORM entities, their field enums and value constants
  use_cases.py  # business operations shared by every adapter
  api.py        # what another feature may call for business
  references.py # ReferenceSpec per named relationship the feature carries
  views.py      # SqlView per ai_* view
  agent.py      # MutationToolSpec, and an AgentSpec if it owns a subagent
  proposal.py   # ProposalHandler
  telegram.py   # the Telegram adapter: screens, editors, the review screen, the citation label
  reducer.py    # reduce(state, action) -> (state, effects), when the feature has a process
```

These names are the whole vocabulary. A feature that wants a file outside this list is saying its
contents belong to a role the list does not have yet, which is a question for the batch, not a new
word.

**The adapter is one file until it is more than one file's worth**, and the rest of Safwa
writes `from .telegram import ...` either way, so the import does not say which it is. Continuity's
four commands are one file; Cards is ten modules. Two names recur inside a package: `screens.py`
is what the owner is taken to, and `handlers.py` is the callback actions the feature publishes.
`review.py` is the `ProposalPresenter` and the citation label. Everything else is the feature's own,
because there is no shared vocabulary of screens to hold it to:

```text
safwa/features/cards/telegram/
  __init__.py      # what the adapter publishes, behind __all__
  presentation.py  # labels, the overview text, list order, the citation
  draft.py         # what a Card written by hand holds, and what stops it being saved
  selectors.py     # which relationships a Card offers, and the two screens that offer them
  creation.py      # the draft's screen and its buttons
  lists.py         # every screen showing several Cards
  screens.py       # the Card itself
  done_gate.py     # the Checks a Card has to answer before it is Done
  text_input.py    # the typed-value flows
  handlers.py      # CARD_CALLBACK_ACTIONS
  review.py        # ProposalPresenter
```

**A screen says how to come back to it as the action that draws it.** A `back` payload is
`{"action": "card_view", "id": 12, ...}` — the callback action plus that action's payload — and
`shell.go_back` dispatches it through the same table every inline button goes through. No module
holds a list of which screens exist, and a screen with no `back` is the menu.

**A screen owns its own transaction.** It opens a session, writes and commits, because a tap is
where a unit of work begins and ends — it mints its single-use `CallbackToken` rows through
`token_button` before it sends. What may never commit is the agent contract, which builds a
proposal and hands it on; Rule K is what says so.

`api.py` **defines** what it publishes. It exists only when another feature actually calls in, and
it hands over the answer rather than the row: `scheduled_memory_time(session)`, not `UserProfile`.
A module that only re-exports is counted as a leftover path by Definition of Done #13.

**Rule E is a layer, and there are three of them.** `model.py` and `api.py` are what a thing is
called; `use_cases.py` is what may be done to it; everything else — the adapter, the agent contract,
the proposal handler, the wiring — is assembly. **A module opens a door at its own layer or below,
never above, in any feature including its own:**

| A module at | may open |
|---|---|
| the vocabulary — `model.py`, `api.py` | `api` |
| the operations — `use_cases.py` | `api`, `use_cases` |
| assembly — everything else | `api`, `use_cases`, `telegram` |

The edge the rule exists to forbid is `api` opening `use_cases`. A door that imports what is built
on top of it has the whole feature behind it, and two such doors facing each other is an import
cycle — which is exactly what `cards/api.py` importing `cards/use_cases.py` would close, through
`planning/api.py`.

The consequence is a door's contents: **a door carries the vocabulary and the reads that need no
operation, and an operation is asked for at the operations layer.** So `checks/api.py` says what a
Check is called, and closing an Action asks `checks/use_cases.py` to answer one. Screens are the
top door because a screen is public already: `FeatureModule.screens` hands `render_card` to the
composition root, and Planning draws its Sprint list with the rows Cards draws.

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

Then one line in `MODULES`. That is the whole edit in central code: the mutation tool and its
schema, the review screen, the view and its allowlist entry, the routing line, the recovery hook and
the background task all follow from the declaration.

The Diary is the pilot for the complete shape. Its proposal handler and Telegram adapter both call
`features/diary/use_cases.py`; its agent input model and presentation constants stay inside the
feature. Reads are deliberately asymmetric: the Advisor reads and opens `ai_diary` directly, while
writes route to the Diary subagent and remain proposals until Save.

## Why the feature's `__init__.py` is empty

`module.py` reaches aiogram, the domain and the Telegram adapters. `views.py` and `api.py` are
leaves that low-level modules import. Keeping the package `__init__` empty is what stops importing a
leaf from executing the manifest, which is how the import graph stays acyclic. The adapter package's
own `__init__.py` is the exception and the reason: nothing imports one of its files to reach a leaf,
so it is free to be the facade that makes a package and a module read the same from outside.
