# BRD scenarios

A BRD scenario is one approved business rule, written Given–When–Then, with an identifier that
outlives any test name. During the migration it is the highest authority on what Safwa does:
when code, tests and docs disagree, an approved scenario wins.

No BDD framework. Readable pytest is enough.

## Files

```text
docs/brd/
  README.md                                  this file
  test_inventory.md                          generated: scripts/test_inventory.py
  planning/cards.md  planning/checks.md  planning/sprint.md
  reminders.md  diary.md  saved_requests.md  proposals.md
  agents.md  telegram_history.md
```

A file is created by the batch that needs it, not up front.

## Migration workflow

`docs/brd/*.md` exists only while a feature is being migrated or refactored. It is the approval
packet, not a permanent BDD suite or a replacement for product documentation.

1. Write the feature BRD in `docs/brd/<feature>.md` and obtain approval.
2. After approval, preserve the accepted scenarios in `tests/brd/<feature>.feature`.
3. Write or rename the relevant unit and E2E pytest tests. Their docstrings cite the scenario ID
   and the `.feature` file, so a failure leads back to the approved rule.

No BDD runner is used. The `.feature` file is a readable traceability contract; pytest executes
the real unit, integration, and E2E checks.

## Identifier

`<AREA>-<TOPIC>-<NNN>`, zero-padded, never reused and never renumbered.

| Area | Prefix | Area | Prefix |
|---|---|---|---|
| Planning | `PL` | Proposals | `PR` |
| Reminders | `RM` | Agents and routing | `AG` |
| Diary | `DI` | Telegram history | `TG` |
| Saved Requests | `SR` | Continuity and memory | `CT` |

## Format

````markdown
### PL-CHECK-014 — An Action cannot be finished while a Check is unanswered

Status: approved
Sources: INITIAL_PLAN §…, CHECKS_PLAN §…
Supersedes: tests/test_checks.py::test_finish_with_checks (implementation_coupled)

Given an Action is in Today and has two Pending Checks
When the owner finishes the Action and answers only one Check
Then the Action stays in Today
And the operation returns the unanswered Checks
And no repeat successor is created
````

The title states the rule, not the mechanism. `Sources` cites the product spec the rule came
from. `Supersedes` names every existing test the scenario replaces, with its class.

The test carries the identifier in its docstring and nothing else:

```python
async def test_pl_check_014_action_with_unanswered_check_cannot_finish(app):
    """PL-CHECK-014"""
```

`app.given` is a dictionary of business fixtures, not a general DSL. It builds data through
public operations and never restates a business rule inside the builder — a builder that
knows the rule makes the test check itself.

## Statuses

| Status | Meaning |
|---|---|
| `draft` | written from code and docs, not yet reviewed |
| `question` | sources conflict; the owner has to decide before it can be approved |
| `approved` | the owner approved it; it is now binding |
| `superseded: <ID>` | replaced by another scenario, kept so traceability does not break |

## When sources disagree

1. an approved BRD scenario
2. authoritative product docs — `INITIAL_PLAN`, `MEMORY_HISTORY_USAGE`, the `*_PLAN.md` files
3. confirmed current E2E behaviour
4. an existing unit or integration test
5. an implementation detail

A conflict is never resolved quietly. It ships in the batch as a `question`.

## Approval

A business batch needs approval **before** its tests are written. The batch opens with a
scenario package of roughly five to fifteen scenarios covering one coherent feature; if
reviewing it means holding Cards, Sprint, Reminders and agent routing in mind at once, the
batch is too large and gets split.

A technical batch needs no approval. The moment one of its tests has to change in what it
expects — not its import path, not a module name — it stopped being technical: stop, write
the scenario, get approval.

## Which level of test

| What is checked | Test |
|---|---|
| A pure calculation or invariant | fast rule test |
| A use case with a transaction and relations | integration on real SQLite |
| Reducer transitions | table-driven transition test |
| Crash, claim, recovery | integration on the stored state |
| Telegram history and message lifecycle | adapter test or E2E |
| Agent routing, resuming after a proposal | E2E with `ScriptedProvider` |
| A provider adapter | contract test |

A scenario has one main test plus lower-level tests for its edges. BRD does not mean E2E.

Use the lowest level that crosses every boundary named by the scenario:

- use a unit test for pure policy and state transitions;
- use an integration test when the rule names persistence, transactions, a real file, or relations;
- require E2E when the outcome depends on application wiring across adapters, serialization, a
  process restart, Telegram message lifecycle, or a provider/tool round trip that a direct use-case
  call cannot observe;
- mock only boundaries outside the rule under test, such as the LLM or Telegram network. Do not mock
  a file, database, scheduler lease, or adapter when that boundary is part of the scenario;
- do not duplicate every BRD scenario as E2E. Add E2E for a critical cross-boundary path, then keep
  boundary conditions at the lower deterministic level.

For example, CO-MEMORY-004 is an integration test rather than E2E: it reads a real temporary
`memory.md` and synchronizes real SQLite state, but it does not claim anything about startup,
watcher scheduling, or Telegram. Those claims would require their own adapter or E2E scenario.

## Audit table

Every batch carries one:

```text
Scenario ID | Existing tests | Class | Decision | New tests | Status
```

Classes are the ones in `test_inventory.md`: `business_valid`, `characterization_valid`,
`implementation_coupled`, `contradictory`, `obsolete`, `missing`. A test is not invalid for
using a Mock or an ORM — invalid means it disagrees with approved behaviour, and nothing else.

A deleted test stays visible in review as "replaced by PL-CHECK-014" rather than vanishing
inside a large mechanical diff.

## Behaviour that does not exist yet

If an approved scenario describes something the code does not do: write the test first, name
the reason and the target batch. A strict `xfail` is allowed only with a scenario identifier
and the phase that removes it. A permanent `xfail` is not allowed.
