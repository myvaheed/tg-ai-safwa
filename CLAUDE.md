# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository. It carries the principles; every
mechanism has a document that owns it, and this file points there instead of keeping a second copy.

## Read what the task needs

`tests/brd/` is what Safwa does, one approved rule per `Scenario`. Read the scenarios for the
feature you are changing before you change it, and [tests/brd/README.md](tests/brd/README.md) for
what a scenario is. Nothing here asks you to read the whole architecture for one task, and neither
the restructuring history ([docs/RESTRUCTURING.md](docs/RESTRUCTURING.md),
[docs/POLISHING_2.md](docs/POLISHING_2.md)) nor the list of unagreed ideas
([docs/FUTURE_FEATURE.md](docs/FUTURE_FEATURE.md)) is required reading for an ordinary task.

| Task | First reading | The check that decides |
|---|---|---|
| Change business behavior | that feature's `.feature`, its `use_cases.py` and `model.py` | the scenario's test, and the adapters the change touched |
| Fix a screen | that feature's `.feature`, its Telegram adapter, [screens.feature](tests/brd/tg_agent_shell/screens.feature) | the UI test, and E2E when the flow crosses a turn |
| Change the loop or routing | [tests/brd/tg_agent_shell/](tests/brd/tg_agent_shell), `agent_runtime/`, [docs/AGENT_ARCH.md](docs/AGENT_ARCH.md) | tool availability, the budgets, suspend and resume |
| Add a feature | [docs/FEATURE_MODULES.md](docs/FEATURE_MODULES.md), then [features/tags](src/safwa/features/tags) for an entity the owner also edits by hand, or [features/diary](src/safwa/features/diary) for one only the model proposes | registration, one scenario, one whole path |
| Remove a feature | its `.feature`, `MODULES`, and whoever calls its `api.py` | no command, view, route or test reference left dangling |
| Build another bot on the shell | "What an application gives the shell" in [docs/AGENT_ARCH.md](docs/AGENT_ARCH.md), and [examples/wallet](examples/wallet/app.py) | `uv run pytest tests\shell -q`, which runs that example with `safwa` unimportable |

[docs/DOMAIN.md](docs/DOMAIN.md) is what a Card, a Check and a Sprint are. `uv run python
scripts/architecture_metrics.py` prints the module graph and the Definition of Done counts, and
naming a feature prints that feature's map instead — no document is kept in step with either.

## Commands

Windows / PowerShell, `uv`-managed, Python pinned to `>=3.12,<3.13`. Setup, the voice extras, backup
and restore are [README.md](README.md); here is what a change is checked with.

```powershell
uv run pytest -q
uv run pytest tests\e2e -q
uv run pytest tests\shell -q            # the example bot on the shell, with no Safwa imported
uv run ruff check .
uv run python scripts/architecture_metrics.py
uv run python scripts/architecture_metrics.py cards   # one feature: its sources, scenarios, tests, views and wiring
uv run pytest tests\test_cards.py::test_parent_stage_propagation_and_reopen -q
uv run pytest --brd=DI-DAY-001 -q       # every test citing one scenario; -m brd runs them all
```

`asyncio_mode = "auto"`, so async tests need no marker. `tests/test_architecture.py` fails on any
architecture-rule violation and on a change to the prompt-prefix, schema or marker-code snapshot
under `tests/snapshots/`. Rewrite one by naming its own test — never the whole file, which
re-baselines the two the batch did not change:

```powershell
uv run pytest tests/test_architecture.py::test_rule_i_prompt_prefix_is_byte_stable --snapshot-update
```

Live Telegram tests are opt-in and skipped without `--live-telegram`. `telegram-bot-exampler/` is an untracked local reference project,
excluded from ruff — never edit it.

## Designing and coding

**Simple is the test of correct.** A right solution is simple. When it is not simple, something is
wrong — go back and find it instead of building around it. A hard problem's right solution is a
composition of simple modular ones, never one complex whole: a monolithic complex solution is a
wrong solution. Several mechanisms that all compensate for one missing property are the signal.

- **The fewest mechanisms that solve the problem.** No abstraction for one call site, no
  configurability nobody asked for, no error handling for an impossible case.
- **Every changed line traces to the request**, or to a declared batch's stated scope. That batch
  deletes the mechanism it replaces, the workarounds that compensated for it, and the vocabulary
  only it read; dead code outside that scope is named, not deleted.
- **State the success criterion first, and make it checkable**: a test that reproduces the bug, a
  scenario that fails without the change, a scanner count that moves.
- Say what you assumed. Ask when the answer changes what you build; otherwise assume and keep going.

## Architecture

Single-owner Telegram bot (aiogram 3) + an OpenAI-compatible LLM (`SAFWA_AI_PROVIDER`, LM Studio by
default, OpenRouter for `openai/gpt-5.6-luna`) + SQLite/SQLAlchemy 2 async. The five packages and
what each owns are [docs/AGENT_ARCH.md](docs/AGENT_ARCH.md). Three facts that decide where an edit
goes:

- Which features exist is [bootstrap/modules.py](src/safwa/bootstrap/modules.py), and nothing else.
  A feature owns its model, use cases, agent contract and Telegram adapter. Where the owner edits
  the entity by hand too, the AI and UI paths call the **same** operations —
  [features/tags](src/safwa/features/tags) is that shape; the Diary is the other one, written
  through proposals alone. A feature is where a change goes, **not a plugin that can be pulled
  out**: Cards, Checks, Values, Tags and Planning read each other through their doors.
- Where a shared thing goes is decided by how many features read it. Cross-feature tuning is
  [constants.py](src/safwa/constants.py), which imports nothing from Safwa; a limit one module owns
  is a constant at the top of that module, and [config.py](src/safwa/config.py) takes its default
  from wherever the limit lives. [enums.py](src/safwa/enums.py) splits the same way.
- [telegram/routing.py](src/tg_agent_shell/telegram/routing.py) registers every handler by name, so
  a handler it does not name is one nothing reaches.

## The rules that outrank a convenient design

Each is a property some mechanism exists to hold. Break one and the mechanism around it stops
meaning anything, so change the mechanism instead.

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
- **A reader is scoped by the view list it declares.** One declaration fills the `{views}` block in
  its prompt and scopes its own `query_data`, so a view no list names is one that reader is refused,
  not merely one it was not told about. Views are rebuilt every startup from the owning feature's
  `views.py`, never migrated.
- **The prompt prefix is byte-stable.** New volatile context goes after the dialogue, never into a
  system block — one timestamp in `messages[0]` costs every cache hit.
- **`data/memory.md` is authoritative.** The `memory_fact_cache` table is a rebuildable derived
  cache; never treat it as the source.
- **One lease, and the owner always wins.** `TurnManager` is the single foreground/background lease;
  background work verifies the revision before it publishes or commits.

## What may change, and what is the owner's

- **Implementation is yours.** So is the shape of the tests, as long as the approved coverage
  survives: a batch that drops a test names what still covers its scenario.
- **Product behavior is the owner's, and so is what an approved `Scenario` means.** An approved
  scenario outranks the code, the tests and every document. A behavior no scenario covers is a
  question to ask, not permission to decide it yourself.
- **Rewording a scenario without changing what it says is editorial, and yours.** The identifier
  never changes; the title after the em dash and the Given/When/Then lines may be made clearer, and
  [tests/brd/README.md](tests/brd/README.md) is what clearer means. Changing which cases a scenario
  covers, or what happens in one, is the owner's.
- **The prompt snapshot travels with the prompt.** A changed system prompt, tool description or
  reader view list updates `tests/snapshots/prompt_prefix.json` in the same batch; no separate
  permission is needed, and the update names that one test.
- **The other two snapshots are each their own declared batch**, because each stamps something
  already outside the repository. A released `MARKS` code is on messages sitting in the owner's
  chat, so a code is added and never reassigned. The schema has no migrations, so a changed column
  reaches a database only by rebuilding it, which is the owner's to do.

## Schema

There are no migrations and no Alembic. The ORM model modules are the schema source, and
`upgrade_database` ([foundation/database.py](src/tg_agent_shell/foundation/database.py)) adds
missing tables and indexes at startup but **never alters an existing one** — so a fresh database
always matches the declared models while a changed column never reaches an existing
`data/safwa.db`. A schema change means editing the owning model and rebuilding the database, backed
up first with `uv run safwa-backup`.

**Do not add Alembic or write migrations before the first release.** The owner recreates the
pre-release database; migration support starts after v1, from the ORM metadata at that point.

## Conventions

- ruff `select = ["E","F","I","UP","B"]`, line length 100, `E501` ignored, target py312. All modules
  start with `from __future__ import annotations`.
- **Everything the model reads is written for a small local model — 4B to 12B.** System prompts,
  tool descriptions, field descriptions, `hint` and `next` are short, imperative and concrete: one
  instruction per line, the exact tool and field names, no rationale and no restating a rule twice.
- **Prose for a developer is not held to that.** A non-obvious decision earns the sentence that says
  why. Code comments stay sparse and explain only a non-obvious *why* — a Telegram or Telethon
  quirk, an ordering constraint.
- Docs are kept current by deleting: a line that stopped being true is removed or replaced in place,
  never left standing next to its correction. One explanation has one home, and a diagram lives
  beside the prose it explains rather than in a gallery of its own. `tests/test_docs.py` checks
  that each link resolves and each code name a document spells still exists — which keeps a
  document readable and says nothing about whether its content is approved.
- User-facing strings are complete sentences and product-specific ("Card", "Sprint", "Value", "Tag",
  "Request" are capitalized domain nouns). Bot messages are HTML — escape any user or model text.
- Enums are `StrEnum` but columns store plain strings — always compare and assign `.value`. Commit
  subjects follow `vX.Y <short summary>`.
- E2E tests use the real SQLite database and real services, replacing only Telegram and the provider
  at their network boundaries — keep new tests on that pattern rather than mocking domain functions.
  QA and live test config never touch production state: `resolve_qa_config`
  ([qa.py](src/safwa/qa.py)) hard-fails on a reused bot token or Telethon session path.

## The rest of `docs/`

Beyond what the table above points at, and none of it required reading for an ordinary task:
[LLM_GATEWAY.md](docs/LLM_GATEWAY.md) is the provider boundary,
[RESTRUCTURING.md](docs/RESTRUCTURING.md) the restructuring candidates still open and the ones
ruled out, [POLISHING_2.md](docs/POLISHING_2.md) the open polishing work — every unfinished item
has one of those two as its owner, never both — and [FUTURE_FEATURE.md](docs/FUTURE_FEATURE.md)
ideas that are agreed nowhere. A feature's own package is a pointer like any other: its `.feature`
file is the rule, and the package is what keeps it. `docs/` is where anything written from now on
goes.
