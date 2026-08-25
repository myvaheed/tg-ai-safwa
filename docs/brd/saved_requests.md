# Saved Requests — approval packet

Status: **approved by the owner on 2026-08-22**, Q1, Q2 and Q3 settled with it.
**Partly superseded on 2026-08-24** by [archive_and_delete.md](archive_and_delete.md): a Request is
deleted, never archived. `SR-AI-009` and `SR-WRITE-003` are gone and `SR-DELETE-013` replaces them.
Batch: Phase 4.c
Sources: `archived_docs/INITIAL_PLAN.md` §"A Saved Request is an AI-created, verified, read-only SQL
query over allowlisted AI views" and §Planning (Backlog narrowed by picked Requests),
`archived_docs/ARCHITECTURE.md` §"Saved Requests reuse the same validator plus two extra rules" and
§Citations, `CLAUDE.md` §"Read-only SQL is triple-guarded", current code and tests.

This file is the approval artifact for the Saved Requests migration batch. The accepted scenarios
move to `tests/brd/saved_requests.feature` once approved — an approved scenario with no test fails
`tests/test_brd_traceability.py`, so nothing enters that file before its test is written.

Eleven scenarios, one batch. Every number is written out with its constant named next to it, and
the tests read the constant — see [README.md](README.md#numbers).

## Scope

In: what a Request is, how one is authored and named, what its SQL is allowed to be, what running
one returns, the AI proposal path, archiving, autoapproval, the `ai_requests` view and citations,
the `/requests` screens, and Requests used as Plan filters.

Out, and why:

- **The read guard itself.** `validate_read_sql`, the read-only connection, the authorizer allowlist
  and the result caps are shared infrastructure with their own tests (`test_ai_sql.py`,
  `test_infrastructure.py`). This batch only says which of them a Request runs behind — see Q1.
- **`parent_query` on the `card` tool.** It reuses the same validator for something that is not a
  Request; it is Planning behaviour and Phase 5 owns it. This batch only keeps the door it calls.
- **The Plan filter rule.** "A Backlog Action is shown only if every picked Request returns it",
  picking nothing versus an empty intersection, and the page reset on toggle are rules of the Plan
  screen; a Request is only where its id set comes from. They belong to the Planning packet in
  Phase 5. The one half that *is* a Request rule — an archived Request stops filtering — is in
  SR-AI-009. `test_telegram_item_ui.py`'s three Plan-filter tests stay green and stay uncited until
  then; traceability fails on an approved scenario with no test, not on a test with no scenario.
- **The `/requests` and Request detail screens as code.** Phase 8 moves the shared handler package,
  whole. Their *behaviour* is in scope here.
- **Re-snapshotting a requeued proposal.** `RequestProposalHandler.version_model = SavedRequest`
  already re-reads; the Phase 2 deferral is about the handlers that do not, and Phase 6 owns it.
- **A proposal applying against state that moved.** This was drafted as a Request scenario and
  withdrawn: for a Request it has no reachable trigger. A screen is never something the owner comes
  back to — **a UI message can only be the last message in the chat and never moves back up**, so
  anything the owner does below a proposal interrupts it, and `cancel_approval_for_target` sets every
  pending proposal in that batch to `REJECTED` before the words reach the Advisor. Inside one batch
  `_refresh_queued_proposal` re-snapshots the expected version and the workspace revision together,
  and a Reminder cannot escalate over a pending proposal at all. On top of that,
  `ProposalService.apply` checks `workspace.revision` before any handler runs, and every Request
  write bumps it — so `RequestProposalHandler`'s own version check would need a writer that moves a
  Request's version without moving the workspace revision, and there is none. The generic rule
  (a proposal applies against the state it was prepared on, or refuses) is Phase 6's, and
  `test_ai_request_update_is_rejected_when_the_request_becomes_stale` stays green and uncited until
  then.

---

## Scenarios

### SR-WRITE-001 — A Request exists only because the model proposed one and the owner saved it

Status: approved
Sources: INITIAL_PLAN §"an AI-created … query"; `commands.command_requests` ("creation intentionally
remains advisor-only")

```gherkin
Given the owner is looking at their saved Requests
When they look for a way to write one by hand
Then there is none: the screens list, open and rerun Requests and nothing else
And the only path that creates one is a `request` proposal the owner saved
And that tool belongs to the board subagent, never to the Advisor
```

### SR-WRITE-002 — A Request name is unique, and case is not a difference

Status: approved
Sources: `SavedRequest.name` is `unique`; `create_saved_request` and `update_saved_request` both
compare `NOCASE`

```gherkin
Given an active Request named "All goals"
When a second Request is saved as "ALL GOALS"
Then it is refused because a Request with that name already exists
And renaming a different Request onto that name is refused the same way
And a name that is empty or only spaces is refused
```

### SR-WRITE-003 — Saving under an archived Request's name brings that one back

Status: approved
Sources: `create_saved_request`, the `existing.archived_at is None` branch

```gherkin
Given a Request named "All goals" was archived, with a description
When a Request is saved under that name again with new SQL
Then the archived Request is the one that comes back, with its original id
And its SQL is the new one
And its description is the one it already had, because none was given
And there is still exactly one Request with that name
```

### SR-SQL-004 — A Request's query is one read-only SELECT, and it must ask for Card ids

Status: approved
Sources: ARCHITECTURE §"reuse the same validator plus two extra rules"; `normalize_request_sql`

```gherkin
Given a Request is being saved
When its SQL is checked
Then it must be a single SELECT or WITH … SELECT over the `ai_*` views
And it must mention `ai_cards`
And it must return a column named `id`
And anything else is refused, and no Request row is written
```

### SR-SQL-005 — The stored statement is checked again every time it runs

Status: approved
Sources: `request_cards` calls `normalize_request_sql` on the stored SQL before executing it

```gherkin
Given a saved Request
When it is run from any surface
Then the stored statement is validated again before it is executed
And a stored statement that no longer passes is refused rather than run
```

### SR-RUN-006 — A Request answers with the live Cards it names, in its own order, each one once

Status: approved
Sources: `request_cards`

```gherkin
Given a Request whose SQL orders its results
When it is run
Then the Cards come back in the order the query returned them
And an id the query returned twice appears once
And an archived Card is not in the answer
And an id that is no longer a Card is skipped rather than failing the run
```

### SR-AI-007 — The screen shows the statement Save will store, and SQL that fails never becomes a proposal

Status: approved
Sources: `RequestProposalHandler.prepare`; ARCHITECTURE §"Tool call → … → ChangePreparer.prepare"

```gherkin
Given the board subagent calls `request` with a `sql` field
When the change is prepared
Then the normalized statement is what the proposal row carries and the review screen prints
And SQL that does not pass comes back as an `unsafe_query` tool error the model can retry
And no proposal row and no Request exist for the refused call
```

### SR-AI-009 — A Request is archived, never deleted

Status: approved
Sources: `RemoveToolInput.validate_target`; `archive_saved_request`

```gherkin
Given the model wants a Request gone
When it calls `remove` for that Request
Then `delete` is refused at the contract, because only a Card allows it
And an approved `archive` hides the Request from `ai_requests`, from the Request screens
And a Plan filter that was picking it stops picking it, without an error
And the Request's name is free for a new Request to reuse — see SR-WRITE-003
```

### SR-AI-010 — Autoapproval may rename a Request; it may never re-aim one

Status: approved
Sources: `DEFAULT_AUTOAPPROVAL_RULES[("request", "update")]` allows `name` and `description` only;
the comment that creation is deliberately absent

```gherkin
Given the model proposes a change to a Request
When autoapproval considers it
Then a `name` or `description` update may be saved without a screen
And a change that touches the SQL always takes the review screen
And a Request creation always takes the review screen
```

### SR-READ-011 — The Advisor reads Requests through the view and cites one with its live count

Status: approved
Sources: `ai_requests`; `context.py` citation line; `screens._citation_label`

```gherkin
Given saved Requests exist
When the Advisor answers
Then it reads them through `ai_requests`, which shows only the ones that are not archived
And it cites one as `[name](request:2)`
And the rendered link carries the Request's name and how many Cards it returns right now
And a citation of an archived Request keeps its words and loses its link
And so does a citation of an id that is no Request at all
```

### SR-UI-012 — /requests is a list, a run and a way back

Status: approved
Sources: `commands.command_requests`, `items.render_saved_request`

```gherkin
Given the owner opens /requests
Then they see every Request that is not archived, by name
When they open one
Then it runs and shows its description and how many Cards it matched
And at most the first 25 of them are listed as buttons (REQUEST_RESULT_LIMIT = 25)
And it says so when there were more
And a Card opened from that list comes back to the Request it was opened from
And Refresh runs the Request again
```

---

## Questions

### Q1 — Does a saved Request run behind the read guard, or only behind validation? (SR-SQL-005, SR-RUN-006)

**Settled by the owner on 2026-08-22: A.** The run path is guarded by validation, and the two
documents that claim otherwise are corrected in this batch. The first round of options was withdrawn
before the decision — see "what the first round missed" below.

The reason is what makes the answer obvious once said: **a Request's result is always a list in the
interface and never enters the model's history.** Every cap in `ReadOnlyQueryRunner` — the 50 rows,
the character budget, the cell and column limits — exists because a local model pays for what it
reads. Nothing on this path is read by a model, so the query needs to be valid and nothing else.

The one number that stays is `REQUEST_RESULT_LIMIT = 25`, and it is not a cap on the query: it caps
how many Cards become buttons on the Request screen. The match count and the Plan filter
intersection are computed from the whole result.

The sources disagree, and this is the one place in the batch where they do.

`CLAUDE.md` and `archived_docs/ARCHITECTURE.md` both say saved Requests sit behind the same triple
guard as `query_safwa`: regex validation, **a separate read-only connection with an authorizer
allowlist**, and **result caps**.

The code does not do that. `request_cards` runs the statement on the ordinary application session:

```python
statement = normalize_request_sql(query_sql, views)
result = await session.execute(text(statement))
```

So on this path the regex validation is the only guard. There is no authorizer, no
`QUERY_TIMEOUT_SECONDS`, and no `DEFAULT_ROW_LIMIT` — `REQUEST_RESULT_LIMIT = 25` caps what the
*screen prints*, after every matching Card has already been loaded. The same validator used by
Planning's `parent_query` *does* go through `context.query_runner`, which has all three.

Writing is not reachable through this hole: the regex refuses every write keyword, refuses a second
statement, and refuses any table that is not an allowlisted view. What is reachable is cost — a
saved Request joining views without a bound runs to completion on the session the bot is using, with
no timeout to stop it, every time the owner opens that screen or the Plan filters recompute.

#### What the first round missed

"Run it through `ReadOnlyQueryRunner`" was recommended before its numbers were read. Three facts
make it something other than a free reuse of an existing guard:

- **`DEFAULT_ROW_LIMIT = 50`.** A Request over a 300-Card Backlog would return 50 ids, silently. The
  Plan filters intersect result sets, so a capped set is a *wrong* Backlog with no error anywhere —
  the silent-failure class `docs/MIGRATION.md` names.
- **Those caps exist for promptability, not for safety.** The runner's own docstring says a local
  model pays for every returned character. A screen and a filter pay nothing.
- **The read-only connection is a second connection to the database file.** Unit tests run on
  `sqlite+aiosqlite:///:memory:`, where there is no file to open, so `tests/test_saved_requests.py`
  and the Plan-filter tests would have to move to a file database. A second connection also cannot
  see uncommitted writes in the caller's session — a change of consistency model, not only of guard.

#### The options as they actually stand

| | A — leave it, fix the docs | B′ — split the guard from the caps | C — bound it on the session |
|---|---|---|---|
| What changes | no code; `CLAUDE.md` and `ARCHITECTURE.md` say the run path is validation-only | `ai/sql.py` gains a read-only-connection-over-the-views object; `ReadOnlyQueryRunner` keeps the prompt caps on top of it, `request_cards` takes the connection and the timeout with its own row bound | keep `session.execute`, wrap the validated statement as `SELECT id FROM (…) LIMIT n` |
| Gains | nothing | authorizer + timeout, without the 50-row cap | a memory bound |
| Costs | a pathological saved query can hang a screen until it finishes | unit tests move to a file database; a Request stops seeing uncommitted session writes | no timeout — a `LIMIT` bounds rows, not time; no authorizer |
| Honest once done? | yes | yes | yes |

Recommendation now: **A**. The residual risk is a legal-but-expensive SELECT, and it has to survive
regex validation, the review screen where the owner reads the SQL, and re-validation on every run.
B′ buys a timeout at the price of a different consistency model and a test-infrastructure change,
inside a batch whose job is to move a module. C buys the wrong bound: `LIMIT` caps memory, and the
danger is time.

If a timeout is wanted, B′ is the only option that actually delivers one, and it is better as its
own small batch than folded into this one.

### Q2 — Must a Request's ids be Card ids? (SR-SQL-004, SR-RUN-006)

`normalize_request_sql` requires the statement to *mention* `ai_cards` and to return a column named
`id`. It does not require that `id` to be the Card's. This passes:

```sql
SELECT k.id FROM ai_checks k JOIN ai_cards c ON c.id = k.id
```

Those are Check ids. `request_cards` then loads Cards by them and returns whatever happens to
collide — usually nothing, occasionally the wrong Cards. The Request says "3 matching cards" and
they are the wrong three. Nothing raises, at authoring time or at run time.

This is why it is worth a decision rather than a shrug: the failure is silent and plausible, not
loud. It is also rare — the SQL is on the review screen when the owner saves it.

| | A — leave it | B — require `ai_cards.id` |
|---|---|---|
| What changes | nothing; SR-SQL-004 says "mentions `ai_cards` and returns `id`" and that is the whole rule | the validator requires the selected `id` to be qualified from `ai_cards`, or unqualified with `ai_cards` as the only source |
| Cost | a wrong query looks like an empty one | some legitimate queries — a CTE over `ai_cards`, a `UNION` — get harder to write, and the model has to be told the shape |

Recommendation: **A**, and say so in SR-SQL-004 rather than leaving it implied. B trades a rare
silent wrong answer for a common awkward one, and the model already writes these under review. If
the owner wants the silence closed without B's cost, the middle option is a *run-time* notice — a
Request whose ids matched no Card at all says so on its screen instead of printing "0 matching
cards" — which is one line in the screen and needs no validator change.

**Settled by the owner on 2026-08-22: A.** The rule stays "mentions `ai_cards` and returns a column
named `id`", and SR-SQL-004 states it rather than implying it. `tool:request` does not move.

### Q3 — Where does `normalize_request_sql` live?

Not a business question; recorded here because it was asked and answered with the others.

**Settled by the owner on 2026-08-22: `ai/sql.py`, next to `validate_read_sql`.** It has two callers
and neither owns it — Saved Requests validates a Request's SQL, Planning validates
`card(parent_query=…)`, which is not a Request. The rule it encodes is a property of the read
surface. See the code-move section.

---

## Audit table

`business_valid` means the test agrees with the scenario above it and needs only its docstring and,
where the module moves, its import path.

| Scenario | Existing tests | Class | Decision | Status |
|---|---|---|---|---|
| SR-WRITE-001 | `test_advisor_flow_e2e.py::test_cacheable_prefix_is_byte_stable_across_turns` (the Advisor's tool list is `query_safwa`, `open`, `route` and nothing else); `test_feature_modules.py::test_a_subagent_only_declares_tools_a_feature_publishes` | business_valid | keep, uncited — both belong to other scenarios | approved |
| SR-WRITE-001 | — the "no manual path" half | missing | **written**: `test_telegram_item_ui.py::test_a_request_is_never_written_by_hand` | approved |
| SR-WRITE-002 | `test_saved_requests.py::test_create_request_restores_an_archived_name` (its tail asserts the create-duplicate refusal) | business_valid | keep; **written**: `::test_a_request_name_is_taken_whatever_its_case` for the update refusal and the empty name | approved |
| SR-WRITE-003 | `test_saved_requests.py::test_create_request_restores_an_archived_name` | business_valid | keep, cited | approved |
| SR-SQL-004 | `test_saved_requests.py::test_saved_request_rejects_non_read_or_non_card_queries` (parametrized over a write, a real table, a non-Card view and a missing `id`) | business_valid | keep, cited | approved |
| SR-SQL-005 | — | missing | **written**: `test_saved_requests.py::test_a_stored_statement_is_checked_again_before_it_runs` | approved |
| SR-RUN-006 | `test_saved_requests.py::test_saved_request_runs_a_safe_card_query`; `test_advisor_flow_e2e.py::test_ai_request_query_values_and_ignores_archived_cards`, `::test_ai_request_query_supports_complex_boolean_logic` | business_valid | keep, cited; **written**: `test_saved_requests.py::test_a_request_returns_each_card_once` | approved |
| SR-AI-007 | `test_advisor_flow_e2e.py::test_ai_creates_an_approved_saved_tag_request` | business_valid | keep, cited; **written**: `::test_ai_request_with_unsafe_sql_never_becomes_a_proposal` | approved |
| SR-AI-009 | `test_ai_sql.py::test_change_from_tool_maps_modes_to_actions` (the "archived, never deleted" half, over `tag` — the same rule at the same door) | business_valid | keep, uncited | approved |
| SR-AI-009 | — the archive *application* | missing | **written**: `test_telegram_item_ui.py::test_archiving_a_request_takes_it_off_every_surface` | approved |
| SR-AI-010 | `test_autoapproval_e2e.py::test_creation_is_never_autoapproved`, `::test_non_allowlisted_operation_does_not_call_the_reviewer` | characterization_valid | keep, uncited; **written**: `test_saved_requests.py::test_a_request_query_is_never_allowlisted_for_autoapproval` | approved |
| SR-READ-011 | `test_advisor_flow_e2e.py::test_ai_can_query_saved_requests_through_the_safe_view` | business_valid | keep, cited | approved |
| SR-UI-012 | `test_telegram_item_ui.py::test_open_item_screen_renders_the_manual_screen_of_every_item`, `::test_citation_codec_matches_every_openable_item_screen` | characterization_valid | keep, uncited; **written**: `::test_the_requests_screen_lists_runs_and_comes_back` | approved |

Tests in these files that belong to other features and are not touched by this batch:
`test_infrastructure.py::test_read_only_query_runner_reads_only_ai_views` reads `ai_requests` but is
a test of the runner, not of Requests; `test_telegram_item_ui.py::test_opening_a_card_from_the_plan_comes_back_to_the_same_page_and_filters`,
`::test_a_filter_that_matches_nothing_says_so_instead_of_an_empty_keyboard` and
`::test_the_filter_screen_toggles_a_request_on_and_off` are the Plan filter rule, which Phase 5 owns;
`test_advisor_flow_e2e.py::test_ai_request_update_is_rejected_when_the_request_becomes_stale` is the
generic stale-proposal guard, which Phase 6 owns — see Scope.

**Eight gaps, all of them behaviour that exists and nothing checks — all eight now written.** None
of the eleven scenarios described behaviour the code did not have, so nothing failed first and no
production line changed for a scenario.

`test_telegram_item_ui.py::test_citations_use_compact_labels_from_saved_items` proves the Request
citation carries its name and live count, and `::test_citations_become_deep_links_only_for_live_items`
proves an archived item loses its link. Both are deliberately **not** cited by SR-READ-011: they run
over every item type at once, and the citations scenario in a later packet is what should own them.

---

## The code move, for approval alongside the behaviour

Nothing here changes behaviour. It is listed because Phase 3 established that the owner approves the
shape of the code as well as the report.

| From | To | Note |
|---|---|---|
| `models.SavedRequest` | `features/saved_requests/model.py` | `safwa.models` keeps the compatibility import, as Diary and Reminders did, so startup still sees the whole metadata |
| `domain.create_saved_request`, `update_saved_request`, `archive_saved_request` | `features/saved_requests/use_cases.py` | the caller keeps owning the transaction |
| `saved_requests.request_cards`, `RequestQueryError` | `features/saved_requests/use_cases.py` | |
| `saved_requests.normalize_request_sql` | `ai/sql.py`, next to `validate_read_sql` | Q3 |
| `src/safwa/saved_requests.py` | deleted | |

Callers that change import path only: `features/saved_requests/proposal.py`,
`features/planning/proposal.py`, `telegram/{items,plan,screens}.py`, and the tests.

`normalize_request_sql` goes to `ai/sql.py` because it has two callers and neither owns it: Saved
Requests validates a Request's SQL, Planning validates `card(parent_query=…)`, which is not a
Request. The rule it encodes — "a read-only SELECT that returns Card ids" — is a property of the
read surface. This also keeps `features/planning` from importing `features/saved_requests` for
something unrelated to Requests.

Q1 settled as A, so `request_cards` is unchanged apart from where it lives. `RequestQueryError`
moves with `normalize_request_sql`: it is that function's error, and both callers already handle it.

**What stays put.** `telegram/items.py::render_saved_request`, `telegram/commands.py` `/requests`
and the `telegram/plan.py` filter screens stay in the shared package — moving a screen into its
feature is what made `features/profile/screens.py` an import cycle, and Phase 8 moves them with the
shared handler mechanism. `REQUEST_RESULT_LIMIT` stays in `constants.py` for the same reason: its
only reader is that screen.

`features/saved_requests/{agent,proposal,telegram,views}.py` and `module.py` already exist from
Phase 2 and are unchanged by this batch except for import paths.

---

## Gates

| | Baseline (4.b.2) | This batch |
|---|---:|---:|
| Tests | 550 passed / 3 skipped | 560 passed / 3 skipped |
| `ruff check .` | clean | clean |
| Entity dispatch points outside `features/` (DoD #1) | 28 | 28 |
| Use case base abstractions (DoD #2) | 0 | 0 |
| Modules over 600 lines (DoD #3) | 5 | 5 |
| Re-export-only modules (DoD #13) | 0 | 0 |
| Import cycles | 0 (453 edges) | 0 (458 edges) |
| Modules under `src/` | 106 | 107 |
| `domain.py` | 1693 | 1608 |

Rules A–F and K at 0, G at 2 and H at 28, all unchanged.

Both snapshots are **byte-identical** and were not regenerated, which is what the batch declared:
nothing here changes a prompt line, a tool contract or a column. `SavedRequest` moved module without
changing a single column type — `archived_at` stays `DateTime(timezone=True)` rather than becoming
the feature-style `UtcDateTime`, because a move that alters the declared schema is not a move.
