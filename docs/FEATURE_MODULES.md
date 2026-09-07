# How a feature plugs in

A Safwa feature declares what it contributes once, in its own `module.py`, and
[`bootstrap/modules.py`](../src/safwa/bootstrap/modules.py) lists it once. Nothing else in the
codebase names a feature: `tests/test_architecture.py` Rule H fails on any module outside
`features/` that spells an entity out.

## What is in that list

Four roles, and one manifest. They are how to read `MODULES`, not four kinds of module: each
declares the same `FeatureModule`, and a role is only what its declaration turns out to hold.

| Role | What one owns | In `MODULES` |
|---|---|---|
| a domain feature | its own rows, the operations every adapter calls, its screens, and the `ai_*` views it publishes | `cards`, `checks`, `values`, `tags`, `planning`, `retro`, `diary`, `profile`, `reminders`, `saved_requests`, `memory` |
| a reader or a writer for the model | a subagent or a helper, the views its own list names and the tools it may call — no rows of its own | `workspace_mutator`, `heavy_analyzer`, and the subagent `diary` declares beside its rows |
| a screen with no rows behind it | a command, the menu, a report the owner reads | `home`, `diagnostics`, `summary` |
| a process of the shell | a flow the shell runs for every feature, registered the way a feature is | `proposals` |

`features/advisor` is in none of them: it is the persona, the root prompt and `ADVISOR_VIEWS`,
which [`bootstrap/modules.py`](../src/safwa/bootstrap/modules.py) assembles itself rather than
plugs in. Rule R names it as the one Safwa package `MODULES` does not register.

## The manifest

[`telegram/manifest.py`](../src/tg_agent_shell/telegram/manifest.py) holds the wiring DTOs,
with the three a feature declares to reach one screen — `ScreenCommand`, `StartLink` and
`TextInputFlow` — in [`telegram/contributions.py`](../src/tg_agent_shell/telegram/contributions.py),
under both the manifest and the container. It is the outermost layer, so it may know aiogram
and SQLAlchemy; nothing that expresses a business rule imports it.

| Field | What it contributes |
|---|---|
| `agents` | an `AgentSpec` — the subagent `route(name)` reaches, and the line the Advisor's prompt carries |
| `helpers` | a `HelperSpec` — the mini session `call_helper(name)` runs, called rather than routed, with the read that earns it and the words it is offered in |
| `proposals` | a `ProposalContribution` per entity: handler, mutation tool, presenter, and which of its actions save without a screen |
| `mutation_tools` | a mutation tool whose change lands on an entity another feature owns (`remove`) |
| `before_tool` | a watcher given each tool call before it runs; a result it returns refuses the call |
| `after_tool` | a watcher given each call and the result it produced, to write on the session or add to that result |
| `views` | the `ai_*` views this feature publishes |
| `screens` | a `ScreenSpec` per item type the owner can be taken to, and how it reads when cited |
| `commands` | a `ScreenCommand` per screen the owner opens by name: a slash command, a menu button, or both |
| `callback_actions` | the inline-button actions this feature's screens draw |
| `text_inputs` | a `TextInputFlow` per editor field the owner types a value into |
| `start_links` | a `StartLink` — a `/start <payload>` this feature answers instead of it opening a cited item |
| `after_turn` | an `AfterTurn` run once the owner's turn has been answered, for work the application does on its own; the shell reads nothing back. Summary's `close_window_after_turn` is the only one |
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

What stays generic is the orchestration: the workspace and its revision, the review and the
approval batch — both held in memory by `ProposalStore`, never rows — the optimistic lock, and the
ordered walk over the changes it holds
([`ChangePreparer`](../src/tg_agent_shell/proposals/prepare.py),
[proposals/use_cases.py](../src/tg_agent_shell/proposals/use_cases.py)).

## Derived registries

[`Registry.of`](../src/tg_agent_shell/registry.py) builds these from `MODULES` at import time and
fails fast on a duplicate entity, tool or view, or on a list without exactly one home screen. The
deriving is the shell's, so a second application gets the same registries from its own list;
`bootstrap/modules.py` publishes them under the names the rest of Safwa reads them by:

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
  hierarchy.py  # Cards only: the derived-value walk, a second module of the operations layer
  api.py        # what another feature may call for business
  references.py # ReferenceSpec per named relationship the feature carries
  views.py      # SqlView per ai_* view
  agent.py      # MutationToolSpec, and an AgentSpec if it owns a subagent
  proposal.py   # ProposalHandler
  telegram.py   # the Telegram adapter: screens, editors, the review screen, the citation label
  background.py # BackgroundTask per loop the polling loop starts and cancels
  <thing>.py    # a long-lived collaborator the composition root builds, named for what it is
```

These are the names that recur, not a closed list. A whole responsibility may take a name of its
own — that is what the last line is — and the question to answer before adding one is whether the
contents are a responsibility or the leftovers of one of the names above. What a new name costs is
concrete: **Rules A, E and K reach a file by name**, so a module the scanner's lists do not know
is a module those rules stop asking about. Splitting the operations layer is three lines in
[architecture_metrics.py](../scripts/architecture_metrics.py) — the name in `BUSINESS_FILES`, in
`MODULE_LAYERS` and in `DOOR_LAYERS` — and `hierarchy.py` is the one that has been paid for: Cards
keeps the walk that writes a parent's derived columns apart from the operations that end at it.

**A reducer is a mechanism, not a file every feature owns.** `reduce(state, action) -> (state,
effects)` earns its place where a pure function makes the transitions readable, and the one
example is the shell's own [`proposals/reducer.py`](../src/tg_agent_shell/proposals/reducer.py):
a press decides the whole move over the approval batch before anything is written, which is what
lets a stale press move nothing. No Safwa feature has one, and none is added without that reason.
Rules C and D hold a `reducer.py` to being pure wherever it appears — inside a feature or inside
one of `PROCESS_PACKAGES`.

**The adapter is one file until it is more than one file's worth**, and the rest of Safwa
writes `from .telegram import ...` either way, so the import does not say which it is. Memory's
three commands are one file; Cards is ten modules. Two names recur inside a package: `screens.py`
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

`api.py` **defines** what it publishes, and it exists only when another feature actually calls in.
Two kinds of thing are on it, and the second is the one that surprises:

- **An answer rather than a row** wherever an answer will do: `scheduled_memory_time(session)`, not
  `UserProfile`.
- **A write of this feature's own rows, driven by another feature's operation**: `attach_values`,
  `attach_tags`, `sync_commitment_for_stage`, `delete_commitments_of_cards`. The Card owns saving a
  Card; the junction row is Values', and the Sprint commitment is Planning's. Saving a Card is one
  operation, so it reaches each of those through its owner's door rather than writing another
  feature's table itself.

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

The consequence is what a door may not hold: **the entity's own operation, which is asked for at
the operations layer.** So `checks/model.py` says what a Check is called — a name another feature
has to type comes from `model.py`, which every layer may reach, and there is no `api.py` standing
in front of it — while closing an Action asks `checks/use_cases.py` to answer one. Screens are the
top door because a screen is public already: `FeatureModule.screens` hands `render_card` to the
composition root, and Planning draws its Sprint list with the rows Cards draws.

`references.py` declares one [`ReferenceSpec`](../src/tg_agent_shell/foundation/references.py) per named
relationship — a Card carries Values, Tags and Checks, a Check carries Values — so a payload key,
its lookup, its junction row and the toggle that writes it are one record. It is separate from
`api.py` because each spec names a toggle from `use_cases.py`, and `api.py` cannot import those:
Planning reads through the Cards door while the Card use cases read through Planning's.

## The two `foundation` packages

They are told apart by who owns what is in them, and neither is renamed: a rename would move every
import for a distinction the namespace already makes.

- [`tg_agent_shell/foundation/`](../src/tg_agent_shell/foundation/models.py) is what every layer
  above may name and no application may fill — the clock, the errors, the base row,
  `upgrade_database`, a `ScreenSpec`, a `ReferenceSpec`, the loop a background task runs in, and
  what kind a bot message is. Nothing in it carries an application's vocabulary, which is what lets
  the package travel.
- [`safwa/foundation/`](../src/safwa/foundation/models.py) is Safwa's own, and only what more than
  one of its features reads: the `Base` its tables hang on, the workspace row and its revision,
  `title_marks`, and the one token estimate every budget is measured against. A type earns its
  place here by having two unrelated features reading it; until then it lives with its owner.

## Who owns the transaction

One rule, because the alternative is a reentrancy question with no good answer:

- A **use case takes an `AsyncSession` and never commits.** It is composable, which is what lets
  `ProposalHandler.apply`, a recovery hook and a Telegram handler all run the same operation.
- The **caller owns the transaction.** Whoever opened the session commits it: a Telegram handler
  around `sessions()`, a background tick, `approve_proposal` around the operations one Save
  applies. Nothing opens a second boundary inside one — an operation a use case has to call takes
  the session it was given, and a nested block would have no savepoint to roll a caught inner
  failure back to.
- A use case whose **answer** turns on the time **takes it as an argument**, and one that only
  records when something happened does not. Profile and Reminders take a `Clock` the composition
  root binds to `SystemClock()`, because the scheduled hour and the next fire are decided against
  the owner's local midnight and timezone. Cards, Checks and Planning call `utcnow()` where they
  stamp `archived_at`, `resolved_at` or `actual_ended_at`, and nothing reads a stamp back to decide
  anything. `sprint_is_due` is the line between the two: it takes an optional `now`, because whether
  a Sprint is over is a decision.

## Registering a feature, and integrating with one

Registering is one line in `MODULES`. Integrating is not: a capability the model reaches has a
publisher and a reader, and the two are separate declarations on purpose. These are the four
edits that come up, and everywhere each one lands.

**A new feature.** The package above, one line in `MODULES`, and `tests/brd/<name>.feature` with
a test citing each of its scenarios — Rule R fails on a package the registry leaves out, and
`tests/test_brd_traceability.py` fails on a package with no scenario file. Everything else is
derived from the declaration: the view and its allowlist entry, the proposal capability, the
routing line, the command, the recovery hook and the background task. If it declares a subagent
or a mutation tool, `tests/snapshots/prompt_prefix.json` gains an entry, which is part of that
batch.

**A mutation tool for an existing subagent.** The `MutationToolSpec` in the owning feature's
`agent.py`, registered on its `module.py` — inside a `ProposalContribution` when the entity is
this feature's, or under `mutation_tools` when the change lands on another feature's entity. Then
the name goes in that subagent's own `AgentSpec.mutation_tools`, in whichever feature declares
the subagent: a tool no subagent names is a tool nothing may call, and a subagent naming a tool
no feature publishes fails the build of the routing rules. The tool's schema is in the prompt
snapshot.

**A view for a reader.** Publishing is the `SqlView` in the owning feature's `views.py` and its
entry in that module's `views`, which creates it at startup and puts it in `ALLOWED_VIEWS`.
**Publishing grants nobody anything.** A reader reaches a view when its own list names it —
`AgentSpec.views`, `HelperSpec.views`, or `ADVISOR_VIEWS` in `features/advisor/agent.py` — and
that same list is what its reads are refused against and what fills `{views}` in its prompt. So
the reader's prompt snapshot changes with it. `ai_card_events` is the shape of this: `cards`
publishes it beside `ai_cards`, and only the Diary subagent and the heavy analyzer read it.

**A screen in the menu.** A `ScreenCommand` on the feature's `commands` — `command` for `/name`,
`nav` for a button, `title` for the words on it, and both fields when it is both. Two more
places: `MENU_LAYOUT` in [`features/home/api.py`](../src/safwa/features/home/api.py) is which row
the button sits in, and a `ScreenSpec` on `screens` is needed only when the model may open or
cite that item type. Nothing goes in
[`telegram/routing.py`](../src/tg_agent_shell/telegram/routing.py): it registers one command
handler over the list the composition root built.

What one feature is already connected to prints from the scanner, so none of this has to be
grepped for:

```powershell
uv run python scripts/architecture_metrics.py cards
```

It reads the registry, the `.feature` file and the test docstrings, and shows the sources, the
scenarios and what cites them, the views it publishes with their readers, the views its own
readers may query, what it declares, and who imports it today. Those are declared links: a
citation is a test's claim on a scenario rather than proof the scenario is checked through, and
the import list is who opens the feature now rather than everything that would break without it.

Two features are worth reading, because they are the two shapes a feature comes in.

**Tags** is where both mutation paths meet: `tags/proposal.py` and `tags/telegram/screens.py` call
the same `create_tag`, `update_tag_fields` and `delete_tag`, so a rule about a Tag is kept once
whichever hand made the change. Copy it whenever the owner may edit the entity themselves.

**Diary** is the complete package with only one of those paths. It declares a model, use cases, an
agent contract, a proposal handler, a view and a subagent of its own — but `diary/telegram.py`
calls no write at all: it renders the day read-only and presents the review, because the Diary is
written through proposals alone. Reads are asymmetric on purpose too: the Advisor reads and opens
`ai_diary` directly, while writes route to the Diary subagent and stay proposals until Save.

## Why the feature's `__init__.py` is empty

`module.py` reaches aiogram, the domain and the Telegram adapters. `views.py` and `api.py` are
leaves that low-level modules import. Keeping the package `__init__` empty is what stops importing a
leaf from executing the manifest, which is how the import graph stays acyclic. The adapter package's
own `__init__.py` is the exception and the reason: nothing imports one of its files to reach a leaf,
so it is free to be the facade that makes a package and a module read the same from outside.
