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
  diary.md  continuity.md  profile_settings.md
  planning/cards.md  planning/checks.md  planning/sprint.md
  reminders.md  saved_requests.md  proposals.md  values_tags.md
  agents.md  telegram_history.md
```

The approved scenarios themselves live in `tests/brd/<feature>.feature`, next to the tests that
cite them. `docs/brd/<feature>.md` is the approval packet around them — `Status`, `Sources`,
`Supersedes`, the recorded decision, the audit table and the gate results.

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
| Saved Requests | `SR` | Continuity and memory | `CO` |
| Profile and Settings | `PS` | | |

`tests/test_brd_traceability.py` reads this table, so a prefix that is not in it is not a
scenario identifier.

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

The test's docstring is the identifier and the file that carries it, and nothing else. The
scenario text lives in the `.feature` file; restating it here would be a second copy to keep
in step.

```python
async def test_pl_check_014_action_with_unanswered_check_cannot_finish(app):
    """PL-CHECK-014 — tests/brd/planning.feature"""
```

`app.given` is a dictionary of business fixtures, not a general DSL. It builds data through
public operations and never restates a business rule inside the builder — a builder that
knows the rule makes the test check itself.

## Language

A `.feature` file is read by someone who has never seen the code. Write it in the owner's words, not
the codebase's.

- Say what happens to the owner, not which function does it. "The Reminder keeps its next fire", not
  "no schedule column changes".
- Name a mechanism only when the rule is *about* that mechanism. DI-READ-013 names its two readers
  because which two the subagent holds **is** the rule; DI-DAY-001 names nothing, because writing
  down a day is not about a function.
- No internal nouns where the owner has a word for it. `provider input`, `cursor`, `hash race`,
  `snapshot`, `workspace revision`, `row` — each has a plain equivalent, and the plain one is right
  unless the rule is about the mechanism.
- Domain nouns stay capitalized and exact: Card, Sprint, Value, Tag, Check, Request, Reminder,
  Summary, Diary.
- **A source is never copied word for word**, `archived_docs/` least of all: it was written quickly
  and carries wrong artifacts. Read what it is getting at, check it against the code, and write that.
  Where the two disagree, the disagreement ships as a `question`.

The identifier line is the exception: it is an identifier, and it never changes wording once
approved. The `Scenario:` title after the em dash is prose and may be made clearer.

## Numbers

A scenario states the number and names the constant next to it. The test reads the constant.

```text
Then the 3 oldest go to the Advisor as one request (REMINDER_FIRE_BATCH = 3)
```

Writing `the batch size` in the scenario says nothing a reviewer can check, and hard-coding `3` in
the test makes the test and the constant two copies that can disagree in silence. Written this way
the scenario is readable on its own, the test still follows the constant when it is tuned, and a
constant that moves away from its scenario is visible to whoever reads the two side by side.

## The traceability is checked

`tests/test_brd_traceability.py` reads the `.feature` files and every test docstring, and
fails on an approved scenario with no test, a test citing a scenario that does not exist, a
docstring in any other shape, a prefix missing from the table above, and a repeated scenario
title. Without it a renamed scenario or a deleted test stays green.

A `.feature` file carries **no Gherkin tags**, and the same test fails on one. No BDD runner
reads these files, so a `@di_day_011` above a scenario is a lowercase second copy of the
identifier that nothing keeps in step. The `Scenario:` line is where the identifier lives.

Two Scenario blocks may share an identifier only when they are two observable cases of the
**same** question — a rule's two branches, or one rule at two doors. Two different rules under
one identifier hide the second one, and no test failure will say so.

The identifier is written in the docstring only. `tests/conftest.py` reads it from there and
attaches the marker, so both of these work with nothing to keep in step:

```bash
uv run pytest -m brd -q
```

```bash
uv run pytest --brd=DI-DAY-001 -q
```

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

Three rules keep "use the lowest level" from becoming "use the cheapest level":

- **A background task, a scheduler, or a startup hook is a boundary.** A rule that only holds
  because a loop runs, a lease is taken, or a recovery hook ran in a particular order needs a test
  at that level. `PS-DIARY-013` is one, and it is why it is not part of `PS-DIARY-012`: the
  reconciliation arithmetic is a use case, but that startup runs it at all is a separate claim,
  and it was untested until it was written down as its own scenario.
- **A failure that would be silent needs a test where the silence would show.** A dead background
  task, a cursor that stops advancing, memory that quietly stops syncing — none of these break a
  unit test, and none of them break the bot loudly either. That is exactly the class that reached
  production in Phase 4.a.
- **During the migration an existing E2E is added to, never replaced by a lower-level test.**
  `tests/e2e/test_advisor_flow_e2e.py` is the insurance for Phases 6–8; a batch that trades it for
  faster unit tests is spending the safety net it is standing on.

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
