# BRD approval packets

`docs/brd/<feature>.md` is the approval packet around one feature's scenarios — `Status`,
`Sources`, `Supersedes`, the recorded decision, the audit table and the gate results. It exists
only while a feature is being migrated or refactored, and this whole folder is deleted at the end
of the migration.

The approved scenarios themselves live in `tests/brd/<feature>.feature` and survive it. What a
scenario has to look like — the identifier, the prefix table, the format, the language, how numbers
are written, and what the traceability test checks — is in
[tests/brd/README.md](../../tests/brd/README.md).

## Files

```text
docs/brd/
  README.md                                  this file
  test_inventory.md                          generated: scripts/test_inventory.py
  diary.md  continuity.md  profile_settings.md
  cards.md  checks.md  planning.md
  reminders.md  saved_requests.md  values_tags.md
  proposals.md  proposals_interrupted.md
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

## Packet header

Each scenario in a packet carries three lines the `.feature` file does not:

````markdown
### CH-GATE-006 — A Card cannot be Done with an unanswered Check

Status: approved
Sources: archived_docs/CHECKS_PLAN.md §…
Supersedes: tests/test_checks.py::test_finish_with_checks (implementation_coupled)
````

`Sources` cites where the rule was read from. `Supersedes` names every existing test the scenario
replaces, with its class. The scenario body itself is written the way
[tests/brd/README.md](../../tests/brd/README.md) says.

## Statuses

| Status | Meaning |
|---|---|
| `draft` | written from code and docs, not yet reviewed |
| `question` | sources conflict; the owner has to decide before it can be approved |
| `approved` | the owner approved it; it is now binding |
| `superseded: <ID>` | replaced by another scenario, kept so traceability does not break |

## When sources disagree

1. an approved BRD scenario
2. confirmed current E2E behaviour
3. an existing unit or integration test
4. `archived_docs/` — a source to read the intent from, never to copy from
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

A deleted test stays visible in review as "replaced by CH-GATE-001" rather than vanishing
inside a large mechanical diff.

## Behaviour that does not exist yet

If an approved scenario describes something the code does not do: write the test first, name
the reason and the target batch. A strict `xfail` is allowed only with a scenario identifier
and the phase that removes it. A permanent `xfail` is not allowed.
