# Proposals, packet one — the life of one proposal

Status: **approved** by the owner on 2026-08-28. Q1 and Q2 were ruled the same day and are
recorded below with what they changed.
Batch: Phase 6.a and 6.b
Sources: `CLAUDE.md` §"AI mutations are always proposals";
`archived_docs/ARCHITECTURE.md` §"AI advisor" (the "The model never mutates" bullet and the two
bullets after it) and §Subagents; current code —
`ai/service.py` `_execute_mutation_tool`, `_materialize`,
`resolve_approval`; [features/proposals/use_cases.py](../../src/safwa/features/proposals/use_cases.py)
`prepare_proposal`, `approve_proposal`, `ProposalStore.end_proposal`;
[proposals/telegram/screens.py](../../src/safwa/features/proposals/telegram/screens.py);
[proposals/telegram/handlers.py](../../src/safwa/features/proposals/telegram/handlers.py)
`_on_approve`, `_on_delete_confirm`, `_on_reject`, `_resume`;
[recovery.py](../../src/safwa/recovery.py); and the E2E suite named in the audit table below.

This file is the approval artifact for the first Phase 6 batch. Nothing enters
[`tests/brd/proposals.feature`](../../tests/brd/proposals.feature) before its test is written — an
approved scenario with no test fails `tests/test_brd_traceability.py`.

Fifteen new scenarios, joining the one that is already approved (PR-TARGET-001). Every number is
written out with its constant named next to it, and the tests read the constant — see
[README.md](README.md) and [tests/brd/README.md](../../tests/brd/README.md#numbers).

---

## What a proposal is, for a reader who has not seen this codebase

Safwa is a single-owner Telegram bot with a language model behind it. The owner can edit their
board by hand, through ordinary Telegram screens with buttons. The model can edit it too — but
never directly.

When the model decides something should be edited, it calls a **mutation tool**. That call writes
nothing. It becomes a **proposal**: a stored record of what Safwa wants done, plus a review screen
in the chat with two buttons, **Save** and **Discard**. Only Save writes anything, and what it
writes goes through the very same code the manual screens use.

Two words appear throughout and mean two different things:

- a **proposal** — one thing Safwa wants done, stored, with its own review screen;
- a **request** — everything the owner asked for in one message. One request can need several
  proposals, reviewed one at a time.

How much goes into one proposal is Safwa's own decision, and nothing in this packet rules on it.
Setting a Card's title and its note at once is one proposal; deciding to do the two separately
makes two. Either is allowed.

Because one request can need several proposals, they form a **queue**. Only the one at the front is
on screen. When the owner decides it, the next takes its place, and Safwa is told nothing until the
last one is decided — then it is told about all of them at once and finishes its answer.

## Scope

**In this packet:** how a mutation tool call becomes a proposal, what the review screen is, how a
queue of proposals behaves, what Save and Discard do, what Safwa is told afterwards, what happens
when a proposal can no longer be applied, and what happens to a tool call Safwa could not turn into
a proposal at all.

**Out, and why:**

- **Words typed over an open review screen.** Interrupting a screen with a new message rejects the
  queue, keeps a subagent's session for one turn, cancels the callers waiting on it, and still
  reports what was already saved. That is packet two, Phase 6.c.
- **Autoapproval.** Whether a screen is shown at all is decided by a separate reviewer. It never
  bypasses the proposal record, and everything in this packet holds whether or not it ran. Packet
  two, Phase 6.c.
- **What each entity's proposal may contain.** That a Goal has no effort, that a Card cannot be
  Done with an unanswered Check, that a Reminder may be removed — each is a rule of the feature that
  owns the entity, and each already has its scenario in that feature's `.feature` file. This packet
  is about the envelope, not the letter.
- **Resuming the model's session.** Claiming a suspended session, replaying its transcript and
  running its loop again are the agent runtime's, and Phase 7 owns them. This packet ends at "the
  queue is empty and here is what happened to each item".
- **Where the receipts the owner reads may and may not go.** That they never enter canonical
  history, summaries or memory is a Telegram-history rule and belongs to a `TG-*` scenario.
- **The `remove` tool's archive-versus-delete rule.** Approved separately in
  [archive_and_delete.md](archive_and_delete.md).

---

## Scenarios

### PR-TARGET-001 — An archived item is not changed automatically

Status: approved, unchanged. Already in `tests/brd/proposals.feature` with its two tests.

### PR-WRITE-002 — Safwa proposes, and only the owner writes

Status: approved
Sources: ARCHITECTURE §"AI advisor", "It has **no mutation tool at all**"; `IMMEDIATE_TOOLS`;
`AIAdvisor._execute_mutation_tool` stores an intent and returns `"status": "prepared"`

```gherkin
Given the owner asks Safwa to change something on their board
When Safwa decides what should be done
Then nothing is written: a review screen appears with the proposal on it
And what it proposes happens only once the owner has saved it
And the tool that proposed it belongs to a subagent, never to Safwa's own voice
```

### PR-SCREEN-003 — A review screen shows everything the proposal would do, behind Save and Discard

Status: approved
Sources: `render_proposal` — "Every proposal screen is read-only: exactly Save and Discard, never a
field control", and its second branch for a proposal holding more than one edit;
`ProposalScreen.blocks` and `.diffs`

Nothing in production makes a proposal that holds more than one edit today — each tool call becomes
one. The second branch is kept as the shape a grouped proposal would take (Q2), and its test builds
that case by hand.

```gherkin
Given a review screen is open on a proposal
When the owner looks at it
Then it names the item and lists what would change, field by field
And it carries exactly two buttons, Save and Discard
When the proposal holds more than one edit
Then every one of them is listed on that same screen, still behind one Save and one Discard
```

### PR-SCREEN-004 — Deleting a Card asks once more before it goes

Status: approved
Sources: `_on_proposal_approve` — "Only a Card deletion needs the extra confirmation"; the
`proposal_delete_confirm` action

```gherkin
Given a proposal to delete a Card
When the owner presses Save
Then nothing is deleted yet: one more screen asks to confirm, saying the whole tree under that
  Card and its contribution to past totals go with it
And the Card is deleted only after the owner presses that confirmation
And no other proposal asks twice
```

### PR-QUEUE-005 — Whatever Safwa proposes at once is one screen

Status: approved
Sources: `prepare_proposal` — "Every mutation call gets its own proposal screen"; one tool call
becomes one `AgentChange` whose `values` carry every field it set

```gherkin
Given the owner asks for something that needs the board changed
When Safwa proposes an edit to one item
Then it becomes one proposal with one review screen, whether it sets one field or five
And whether Safwa proposes two edits to one item together or apart is its own choice,
  and no rule here decides it
```

### PR-QUEUE-006 — Several proposals from one request are reviewed one at a time, in the order Safwa made them

Status: approved
Sources: ARCHITECTURE §"AI advisor", "Multiple mutation calls in one turn become independent queued
proposal screens in call order"; `_materialize` writes `Proposal {position}/{len(targets)}`

```gherkin
Given one request from the owner needs Safwa to propose three times
When the proposals are put together
Then there are three review screens, not one screen listing three items
And they are queued in the order Safwa made them, not sorted or grouped
And each is headed with its place in the queue, "Proposal 2/3"
And a proposal that is alone in its queue carries no such heading
```

### PR-QUEUE-007 — Only the front of the queue is on screen, and Safwa answers after the last one

Status: approved
Sources: ARCHITECTURE §"AI advisor", "The model resumes only after the last item resolves … and
receives all mutation and read results"; `resolve_approval` picks the next pending item

```gherkin
Given three proposals from one request are queued
When the owner saves or discards the one on screen
Then the next one in the queue takes its place on screen
And Safwa says nothing about the request while any of them is still undecided
And once the last one is decided, Safwa is given every decision at once and then answers
```

### PR-QUEUE-008 — Saving one proposal does not spoil the ones behind it

Status: approved
Sources: `_refresh_queued_proposal` — "Snapshot a proposal when it becomes visible after earlier
batch decisions"

Read next to PR-STALE-012, which refuses a proposal once the board has moved on without it: the
owner's own approvals inside one request are not the board moving on. What each proposal would
write stays exactly as Safwa prepared it.

```gherkin
Given Safwa proposed three edits to the same Card, and all three are queued
When the owner saves the first one
Then the second one is still saveable, and saving it edits the Card as it now is
And the third one is still saveable after that
```

### PR-SAVE-009 — Save writes through the same operations the manual screens use

Status: approved
Sources: CLAUDE.md §"AI mutations are always proposals"; `approve_proposal` and each feature's
`ProposalHandler.apply`

```gherkin
Given a proposal to move an Action to Done
When the owner saves it
Then the Action moves exactly as it would if the owner had pressed Done on its own screen
And everything that follows from that follows too, its Card tree and its Sprint included
And the review is over, and its screen can no longer be acted on
When a proposal holds more than one edit
Then they are applied in the order the review screen listed them
```

### PR-SAVE-010 — Discard writes nothing, and the rest of the request goes on

Status: approved
Sources: `_on_proposal_reject` → `ProposalStore.end_proposal`; `_DECISION_NEXT_STEPS[BatchDecision.DISCARDED]`

```gherkin
Given a proposal is on screen and two more from the same request are queued behind it
When the owner discards it
Then nothing about that item changed
And the review is over, and its screen can no longer be acted on
And the two behind it are still queued and still shown in turn
```

### PR-RESULT-011 — After each decision Safwa is told what became of that proposal

Status: approved
Sources: CLAUDE.md §"AI mutations are always proposals" — "Anything the model must know across an
approval belongs in a tool result, not in a receipt"; `_resolved_tool_result` and
`_DECISION_NEXT_STEPS`

```gherkin
Given the owner saved one proposal and discarded another from the same request
When Safwa carries on with that request
Then for each one it is told the decision, which item it was and what it would have done
And for the saved one it is told not to propose it again
And for the discarded one it is told that it did not happen
```

### PR-STALE-012 — A proposal is refused once the board has moved on without it

Status: approved
Sources: `approve_proposal` compares `Workspace.revision` before anything is applied;
`StaleStateError`; the `proposal_` branch of the callback error handler

A Sprint closes itself once it passes its planned end date, on a background poll with nobody
present, and closing it also archives what has settled. A review screen is only ever dismissed by
something the owner does — a command, a button, typed words — so a screen left standing overnight is
still there in the morning, waiting on a board that moved without it.

```gherkin
Given a proposal is on screen, and the owner leaves it unanswered overnight
When the Sprint reaches its planned end and closes itself in the night
And the owner presses Save in the morning
Then nothing at all is written
And the owner is told the board has moved on since Safwa proposed this, and it has to be
  proposed again
And the screen cannot be acted on any more
```

### PR-STALE-013 — A proposal lives for one running process

Status: approved
**Behaviour change, ruled by the owner on 2026-08-29.** A proposal has no age limit while the
process that created it is still running. A restart ends every unanswered proposal review and takes
its buttons with it, so an old button answers like any dead screen. A resolved proposal and its
stored changes are deleted after their result has moved into the approval batch.

```gherkin
Given a proposal was made in the current running process and was never answered
When the owner presses Save, however long that process has stayed alive
Then the proposal is applied normally
And the review is over
When Safwa restarts instead before the owner answers another proposal
Then its review, its queue and its buttons are all gone
And pressing the old Save button writes nothing and says the screen has to be reopened
```

### PR-FAIL-014 — A proposal that fails while it is being saved takes nothing else with it

Status: approved
Sources: `_resume_failed_approval` resolves the item as `failed`; `resolve_approval` records the
proposal as failed; `_report_callback_failure` puts the screen back when there is no waiting
request; `_DECISION_NEXT_STEPS["failed"]`

```gherkin
Given three proposals from one request are queued and the second one cannot be applied
When the owner saves it
Then it is recorded as failed and nothing was written for it
And the first one, already saved, stands
And the third one is still queued
And Safwa is told to fix only that one and try it once more
When the same failure happens to a proposal no request is waiting on
Then the proposal stays as it was and its screen comes back with the reason on it
```

### PR-REPAIR-015 — A tool call Safwa could not turn into a proposal comes back as an error, not a screen

Status: approved
Sources: `_execute_mutation_tool`'s `invalid_arguments` result; `_materialize`'s
`ToolPreparationError` branch; `_with_queued_siblings`

```gherkin
Given one request where two of Safwa's three calls are well formed and the third is not
When the proposals are put together
Then the two well-formed ones are queued for review
And the third opens no screen at all
And Safwa is told what was wrong with it and what to send instead
And Safwa is told its other calls were not cancelled and are waiting for the owner
```

### PR-REPAIR-016 — After five tries Safwa stops rather than keep guessing

Status: approved
Sources: `MAX_REPAIR_ROUNDS`; `_materialize`'s `repair_exhausted`; `resolve_approval`'s exhausted
branch

```gherkin
Given Safwa keeps sending a call it cannot get right (MAX_REPAIR_ROUNDS = 5)
When the fifth attempt fails as well
Then Safwa stops trying and says it could not prepare that one
And it says that nothing unfinished was applied
And whatever it did get right in the same request is untouched
```

---

## What the owner ruled, 2026-08-28; amended 2026-08-29

### Q1 — A proposal is valid for exactly one running process

Proposal rows are active review state, not history. While the process that created a proposal is
running, its Save and Discard buttons remain valid without an age limit. Proposal callback actions
therefore ignore the generic callback-token TTL; single-use claiming still prevents a double press.

At startup Safwa deletes every unanswered proposal and approval batch, deletes the buttons those
reviews left in the chat, and abandons every run that was waiting on one. An old button then answers
"This action expired. Reopen the screen." like any other dead screen. SQLite proposal IDs use
`AUTOINCREMENT`, so a consumed button or a stored screen that still names a resolved proposal can
never reach a later one.

**Ruled:** there is no proposal expiry by age. Save, Discard, stale refusal and interruption delete
the review; a completed or interrupted approval batch is closed once its result has moved to the
agent transcript.

### Q2 — A proposal may hold several edits, and the screen shows all of them

`ChangeProposal.changes` is a list: one proposal may hold many edits, in order. Applying walks that
list, and the review screen has a second branch that lists every one when there is more than one.
Nothing in production adds a second, so that branch has never run.

**Ruled: keep it.** If a proposal ever holds several edits, the owner sees all of them before
deciding. The column, the ordered walk and the second screen branch all stay, and PR-SCREEN-003 and
PR-SAVE-009 now say what they guarantee.

### Withdrawn — what the owner reads on each screen of a queue

Raised and withdrawn before approval. Every screen in a queue carries Safwa's prose for the whole
request, with only the `Proposal 2/3` heading telling them apart, so the owner reads the same
paragraph once per screen. Ruled: that prose is the plan for the request and the owner should keep
seeing all of it. No change, and no scenario.

---

## Audit

Every scenario below already has behaviour in the code and tests standing behind it. None of those
tests cites a scenario yet, so none of them is deleted: they gain a docstring naming the identifier,
and the ones marked `missing` are written in the batch.

| Scenario | Existing tests | Class | Decision | New tests | Status |
|---|---|---|---|---|---|
| PR-WRITE-002 | `test_subagent_e2e.py::test_the_board_owns_every_mutation_tool`, `::test_a_routed_subagent_is_offered_only_its_own_tools`; `test_advisor_flow_e2e.py::test_read_and_mutation_in_one_turn_rejects_only_the_mutation` | business_valid | cite | — | approved |
| PR-SCREEN-003 | `test_advisor_flow_e2e.py::test_single_tag_proposal_save_and_discard_callbacks_resume_agent` | business_valid | cite, extend to assert the button set | 2 adapter tests: the one-edit screen, and a hand-built proposal holding two edits | approved |
| PR-SCREEN-004 | none | missing | write | 1 E2E: Card delete proposal, Save, confirm | approved |
| PR-QUEUE-005 | `test_advisor_flow_e2e.py::test_ai_create_tag_and_links_are_reviewed_as_separate_proposals`, `::test_ai_create_value_and_link_are_reviewed_as_separate_proposals` | business_valid | cite | 1 integration test: one call setting several fields is one proposal and one screen | approved |
| PR-QUEUE-006 | `test_advisor_flow_e2e.py::test_multiple_ai_card_creations_are_reviewed_sequentially`, `::test_independent_mutations_are_reviewed_in_order_before_one_resume` | business_valid | cite | — | approved |
| PR-QUEUE-007 | `test_advisor_flow_e2e.py::test_mixed_query_and_mutation_resumes_only_after_approval`, `::test_read_queries_beside_a_proposal_still_resume_the_agent`, `::test_query_then_link_continuation_can_suspend_for_a_second_queue` | business_valid | cite | — | approved |
| PR-QUEUE-008 | `test_autoapproval_e2e.py::test_next_head_is_autoapproved_after_a_manual_save` (partial: it covers the reviewer, not this) | implementation_coupled | keep, cite elsewhere | 1 E2E: three queued edits to one Card, all three saved in turn | approved |
| PR-SAVE-009 | `test_advisor_flow_e2e.py::test_ai_stage_update_to_done_keeps_completion_accounting`, `::test_ai_card_proposal_reaches_the_sprint_it_was_planned_into`, `::test_ai_approved_tag_proposal_creates_a_reusable_tag` | business_valid | cite | — | approved |
| PR-SAVE-010 | `test_advisor_flow_e2e.py::test_discarded_proposal_result_is_returned_with_later_approval`, `::test_discarding_the_last_queued_proposal_still_reports_saved_siblings` | business_valid | cite | — | approved |
| PR-RESULT-011 | `test_advisor_flow_e2e.py::test_resumed_request_replays_its_own_intermediate_steps`, `::test_a_resolved_proposal_leaves_one_readable_line_in_the_dialogue` | business_valid | cite | — | approved |
| PR-STALE-012 | `test_advisor_flow_e2e.py::test_ai_request_update_is_rejected_when_the_request_becomes_stale` | business_valid | cite | 1 integration test on the workspace check itself | approved |
| PR-STALE-013 | `test_proposals.py::test_a_proposal_does_not_expire_while_its_process_is_running`, `::test_startup_discards_every_unanswered_proposal_review`; `test_advisor_flow_e2e.py::test_proposal_save_ignores_button_age_while_the_process_is_running`, `::test_restart_invalidates_an_unanswered_proposal_button` | business_valid | cite; behaviour change (Q1) | no-age Save, startup invalidation of the review and its buttons, and non-reused IDs | approved |
| PR-FAIL-014 | `test_advisor_flow_e2e.py::test_single_proposal_save_error_is_reported_and_resolved`, `::test_proposal_ui_queues_mutations_and_reports_dependency_failure`, `::test_child_proposal_fails_cleanly_when_earlier_parent_is_discarded` | business_valid | cite | 1 for the branch with no waiting request | approved |
| PR-REPAIR-015 | `test_advisor_flow_e2e.py::test_invalid_create_returns_minimal_repair_arguments_to_the_model`, `::test_failed_call_result_states_that_its_siblings_are_still_queued`, `::test_new_tag_and_dependent_card_link_use_one_repair_round` | business_valid | cite | — | approved |
| PR-REPAIR-016 | `test_advisor_flow_e2e.py::test_mutation_repair_loop_stops_after_five_rounds` | business_valid | cite | — | approved |

Two gaps this packet originally found by reading were the final confirmation on a Card deletion and
proposal lifetime. Both now have executable scenarios; lifetime is process-bound rather than timed.

## What the batch changes besides tests

- Proposal age and session-idle expiry are removed. Proposal actions ignore the generic callback
  TTL while their creating process is alive.
- Reviews and approval batches live in `ProposalStore`, so a restart has already ended them.
  Startup deletes the proposal buttons they left, clears the `related_id` of their screens, and
  abandons every run waiting on one.
- Proposal IDs are never reused by SQLite, so an old Telegram button cannot alias a new proposal.
- Nothing is removed for Q2: the `position` column, the ordered walk and the screen's second branch
  all stay, and gain the tests that say what they do.

## Gates

Measured after 6.a and 6.b.

| | Before | After |
|---|---|---|
| `uv run pytest -q` | 728 passed / 3 skipped | 752 passed / 3 skipped |
| `uv run ruff check .` | clean | clean |
| `ai/service.py` lines | 2286 | 2161 |
| Modules over 600 lines (DoD #3) | 5 | 5 |
| Import cycles | 0 (638 edges) | 0 (656 edges) |
| `prompt_prefix.json` | must not move | unchanged |
| `schema.json` | moves on the tables that migrate | the three proposal tables removed |

Rules C and D were vacuous at 0 because nothing in the codebase was a reducer. They now read
seven frozen classes in `proposals/model.py` and the one `reduce` in `proposals/reducer.py`, and
still report 0.

## What the batch delivered

- `ChangeProposal` and `ProposalChange` moved to
  [features/proposals/model.py](../../src/safwa/features/proposals/model.py), and `ApprovalBatch`
  joined them: the batch is a table of its own instead of an `AgentStep` row whose status lived in
  JSON. `SUSPENDED_BATCH_LOOKUP_LIMIT` is gone with the scan it bounded, and neither row carries a
  status: existence is pending, and a batch is over when no screen is still waiting.
- [reducer.py](../../src/safwa/features/proposals/reducer.py) decides every transition of a batch,
  reads nothing and writes nothing, and is covered by a transition table in
  [tests/test_proposal_batch.py](../../tests/test_proposal_batch.py).
- `ProposalService` is gone. `prepare_proposal` and `approve_proposal` are use cases in
  [use_cases.py](../../src/safwa/features/proposals/use_cases.py), called by both the callbacks and
  the agent; ending a review is `ProposalStore.end_proposal`.
- Proposal reviews are process-bound working state: no age expiry, terminal rows are deleted, and
  startup invalidates every unanswered review together with its buttons.
- Both stale refusals now say what happened in the owner's words rather than "Planning state
  changed", which named the mode with no Sprint rather than the board.
