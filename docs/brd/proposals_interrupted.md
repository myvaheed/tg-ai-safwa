# Proposals, packet two — the review the owner never answers, and the one they never see

Status: **approved** by the owner on 2026-08-28.
Batch: Phase 6.c
Sources: `CLAUDE.md` §"AI mutations are always proposals"; current code —
[ai/service.py](../../src/safwa/ai/service.py) `_advance_autoapprovals`,
`_autoapproval_candidate`; [ai/autoapproval.py](../../src/safwa/ai/autoapproval.py);
[telegram/_messaging.py](../../src/safwa/telegram/_messaging.py) `dismiss_prior_ui`;
[telegram/dialogue.py](../../src/safwa/telegram/dialogue.py); and the E2E suite named in the audit
table below.

Seven scenarios. Packet one — [proposals.md](proposals.md), approved 2026-08-28 — covered a
proposal the owner answers. This one covers the two cases where they do not: the review they walk
away from, and the one Safwa decides not to show them at all.

Six of them write into [`tests/brd/proposals.feature`](../../tests/brd/proposals.feature). The
seventh is not about proposals at all and opens a new file, `tests/brd/screens.feature` — it is here
because it was found while reading this one, and it has no other packet.

Every number is written out with its constant named next to it, and the tests read the constant.

---

## What this packet is about, for a reader who has not seen this codebase

Packet one described the ordinary life of a proposal: Safwa proposes, a review screen appears with
**Save** and **Discard** on it, and only Save writes anything. Two things it left out.

**The owner does not have to answer.** They can write a new message, run a command, or open another
screen instead. Telegram is a single column of messages, so a screen is only ever the last thing in
the chat — anything the owner does afterwards is below it, and the review they walked past would
sit there for days waiting on a board that keeps moving. So it does not wait: acting anywhere else
ends it. That is not a rule about proposals, which is why it gets its own file.

**Some proposals are never shown.** Safwa can save a change by itself — *autoapproval*. It is
switched on or off as a whole, and when it is on it works in two steps. First a fixed list decides
whether this kind of change is eligible at all: the operation has to be on the list, and every
field the change sets has to be one that operation is allowed to set. Only then is the proposal
read a second time against the owner's own words, to judge whether it is exactly what they asked
for. Any doubt, and the screen appears as usual. Nothing about the record changes either way — the
proposal is stored and marked saved exactly as if the owner had pressed the button themselves.

## Scope

**In this packet:** what happens to a review the owner walks away from, what they are told
afterwards, which screen survives such an action, and the whole of autoapproval.

**Out, and why:**

- **Everything in packet one.** How a proposal is made, what its screen shows, how a queue is
  reviewed, what Save and Discard do, and what happens when one can no longer be applied.
- **Which changes autoapproval may save.** The list is deliberately a data table in the code
  (`DEFAULT_AUTOAPPROVAL_RULES`), and adding an entry is a decision per entry, not a rule of this
  packet. What the scenarios fix is that the list governs, and that a new item is never on it.
- **What becomes of unfinished work when the owner interrupts.** Reading this packet is what turned
  that into an open design question rather than a rule: today the interruption drops the plan the
  Advisor had made and keeps the subagent's, and the un-started half of a two-part request is lost
  with nothing said about it. The decision is recorded in
  [MIGRATION.md](../MIGRATION.md) §"Phase 7 notes" and is built there, with its own scenarios.
  Nothing about it is approved here, because approving it would approve the defect.
- **How a suspended request is picked up again.** Claiming a paused turn and replaying what it had
  already done belong to the agent runtime, which Phase 7 owns.
- **What Safwa says in its own answer afterwards.** The wording of a reply is not a rule.

---

## Scenarios

### PR-INTERRUPT-017 — Writing instead of answering ends the review

Status: approved
Sources: `dismiss_prior_ui`; `dialogue.py` dismisses the open screens before it starts the turn

```gherkin
Given a review screen is in the chat and two more proposals from that request are queued behind it
When the owner writes a new message instead of pressing Save or Discard
Then none of the three is written
And each of them is recorded as discarded
And the screen loses its buttons and becomes a plain account of what the request did
And the message they wrote is answered as their next request
```

### PR-INTERRUPT-018 — What was already saved is still reported as saved

Status: approved
Sources: the interrupted request's consolidated result; `dismiss_prior_ui` renders it under
"Request interrupted"

```gherkin
Given three proposals from one request, the first of them already saved
When the owner writes a message instead of deciding the second
Then the first one stays saved
And the account left in place of the screen names all three, the saved one included
```

### SC-LIVE-001 — Only one screen is live, and it is the one the owner just acted on

Status: approved
Sources: `dismiss_prior_ui` — "Leave exactly one interaction screen live: the one this event belongs
to"; the command middleware calls it before every command

```gherkin
Given a dashboard, a Card editor and a review screen were all opened earlier in the conversation
When the owner writes a message, runs a command, or walks into another screen from a menu
Then every screen except the one that action belongs to stops being answerable
And a dashboard or an editor is taken out of the chat, while a review screen stays as a written
  account of what became of it
And the screen that action belongs to is left alone
When the owner presses a button that only redraws the screen they are already on, such as turning
  its page
Then no screen is ended by that
```

### PR-AUTO-024 — Autoapproval saves a change with no screen when it is exactly what was asked for

Status: approved
Sources: `_advance_autoapprovals`; `AutoApprovalRule.accepts`; `AUTOAPPROVAL_PROMPT`;
`_resolved_tool_result` — "The user pressed nothing, so 'you saved it' would be wrong in the reply"

```gherkin
Given autoapproval is switched on
And the owner asked for a change to an item they already have
When Safwa proposes that change, and both the operation and every field it sets are ones
  autoapproval is allowed to save
Then the proposal is read against the owner's own words, and saved only if it is exactly what they
  asked for
And it is saved with no screen ever shown, stored and recorded the way one the owner saved by hand is
And Safwa is told autoapproval saved this one and that the owner decided nothing
```

### PR-AUTO-025 — Autoapproval never covers a new item, and never an unlisted change

Status: approved
Sources: `DEFAULT_AUTOAPPROVAL_RULES` — "Creation is deliberately absent"; `rule_for` returns nothing
for an operation or a field that is not listed

```gherkin
Given autoapproval is switched on
When Safwa proposes to create an item the owner does not have yet
Then its review screen is shown, and the proposal is never read against their words
When Safwa proposes an operation that is not on the list, or one that sets a field outside what
  that operation may set
Then that one is shown as well, and it is not read against their words either
```

### PR-AUTO-026 — Doubt leaves the proposal exactly as it was

Status: approved
Sources: `AutoApprovalReviewer.review` returns "show it to them" on doubt and on its own failure;
`_advance_autoapprovals` keeps the outcome it was given

```gherkin
Given autoapproval read a proposal that could be read in more than one way
When it decides the owner should see it
Then the proposal stays exactly as it was prepared, and its review screen is shown
When autoapproval cannot reach a decision at all
Then the review screen is shown for that reason instead, and nothing about the proposal changed
```

### PR-AUTO-027 — In a queue, autoapproval reads one proposal at a time, at the front

Status: approved
Sources: `_autoapproval_candidate` builds the request-only view for the active head;
`_advance_autoapprovals` resolves it and reads the new head

```gherkin
Given one request from the owner becomes three proposals, queued in the order Safwa made them
When autoapproval saves the first and the second has to be shown
Then the second is on screen, and the third has not been read by autoapproval at all
When the owner decides the one on screen
Then the third is read then, and autoapproval may still save it with no screen of its own
```

---

## Audit

Most of this behaviour is already in the code and already has tests standing behind it; none of
those tests cites a scenario yet, so none is deleted. Four branches have no test, and those are the
new tests in the batch.

| Scenario | Existing tests | Class | Decision | New tests | Status |
|---|---|---|---|---|---|
| PR-INTERRUPT-017 | `test_advisor_flow_e2e.py::test_new_dialogue_cancels_every_unresolved_item_in_suspended_batch`; `test_telegram_item_ui.py::test_new_dialogue_discards_and_freezes_pending_proposal` | business_valid | cite | 1 adapter test: typed words end the review and then get answered | approved |
| PR-INTERRUPT-018 | `test_advisor_flow_e2e.py::test_new_message_discarding_a_queue_reports_what_was_already_saved` | business_valid | cite | — | approved |
| SC-LIVE-001 | `test_advisor_flow_e2e.py::test_navigating_away_freezes_the_proposal_into_the_same_outcome_text`; `test_telegram_item_ui.py::test_a_command_dismisses_every_other_screen` | business_valid | cite | 1 adapter test: the screen the owner walked into is left alone | approved |
| PR-AUTO-024 | `test_autoapproval_e2e.py::test_an_exact_allowlisted_edit_is_autoapproved` | business_valid | cite | — | approved |
| PR-AUTO-025 | `test_autoapproval_e2e.py::test_creation_is_never_autoapproved`, `::test_non_allowlisted_operation_does_not_call_the_reviewer` | business_valid | cite | — | approved |
| PR-AUTO-026 | `test_autoapproval_e2e.py::test_reviewer_doubt_leaves_the_original_proposal_pending` | business_valid | cite | 1 test: autoapproval itself fails and the screen still stands | approved |
| PR-AUTO-027 | `test_autoapproval_e2e.py::test_batch_is_reviewed_head_first_without_a_bulk_block`, `::test_next_head_is_autoapproved_after_a_manual_save`, `::test_multi_step_request_can_be_autoapproved_one_proposal_at_a_time` | business_valid | cite | 1 test: three proposals, and the third is not read while the second is on screen | approved |

Four gaps this packet found by reading. Nothing checks that the screen the owner walked into
survives the dismissal of the others. Nothing checks that typed words both end the review and then
get answered, rather than only ending it. Nothing checks what happens when autoapproval cannot
reach a decision at all — the branch that exists so a failure there costs the owner a button press
rather than their data. And no test has three proposals in a queue, so "one at a time, at the
front" is demonstrated only at two positions and never that the one behind is left unread.

## What the batch changes besides tests

No behaviour changes.

- `tests/brd/screens.feature` is created for SC-LIVE-001, and `SC` is added to the prefix table in
  [tests/brd/README.md](../../tests/brd/README.md), which `test_every_scenario_prefix_is_declared_in_the_readme`
  requires.
- `cancel_approval_for_proposal` and `resolve_approval` are each two jobs in one function: what
  happens to the batch, and what happens to the paused turn. The first half moves into
  [features/proposals/use_cases.py](../../src/safwa/features/proposals/use_cases.py) beside
  `approve_proposal`, over the reducer that already decides these transitions. The second half stays
  in [ai/service.py](../../src/safwa/ai/service.py) for Phase 7, which owns claiming and resuming a
  turn — and which rewrites it.
- That is the seam [MIGRATION.md](../MIGRATION.md) draws for Phase 6: a batch that reaches into a
  running turn has crossed it.

## What the batch delivered

Seven scenarios, twelve citations on tests that already stood, four new tests for the branches
nothing covered, and `tests/brd/screens.feature` with the `SC` prefix declared beside it.

The batch half of `resolve_approval` and `cancel_approval_for_proposal` is now `decide_batch_item`
and `interrupt_batch` in [features/proposals/use_cases.py](../../src/safwa/features/proposals/use_cases.py),
with `_refresh_queued_proposal` moved beside them. What the model reads back is still rendered in
[ai/service.py](../../src/safwa/ai/service.py) and handed in, because the labels belong to the
session layer. Claiming and resuming a turn stayed there for Phase 7.

**The bonus the owner asked for, outside the scenarios:** the `board` and `diary` prompts now say to
write one line naming what is about to be done before calling the tool. A session keeps a plan only
as far as the model wrote it into `content`, so this is what makes the plan survive a suspension —
see [MIGRATION.md](../MIGRATION.md) §"Phase 7 notes". It moves the prompt prefix on purpose, and
nothing else in the snapshot moved with it.

## Gates

| | Before | After |
|---|---|---|
| `uv run pytest -q` | 752 passed / 3 skipped | 759 passed / 3 skipped |
| `uv run ruff check .` | clean | clean |
| `ai/service.py` lines | 2161 | 2085 |
| Modules over 600 lines (DoD #3) | 5 | 5 |
| Import cycles | 0 (656 edges) | 0 (656 edges) |
| `prompt_prefix.json` | `agent:board` and `agent:diary` move with the bonus | only those two hashes changed |
| `schema.json` | must not move | unchanged |
