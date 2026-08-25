# Checks — approval packet

Status: **approved** 2026-08-24. Scenarios preserved in `tests/brd/checks.feature`.
Batch: Phase 5.c
Sources: `archived_docs/INITIAL_PLAN.md` §§"A Check records a state observation", "Pending is derived
from an unanswered Check", "A repeatable Check produces a fresh Pending successor", "A Card cannot be
completed while it has Pending Checks"; `CLAUDE.md` §Domain invariants; `create_check`,
`resolve_check`, `_spawn_check_successor`, `_clone_checks_for_successor`, `_pending_check_resolutions`,
`finish_action`; the tests in the audit.

The third Phase 5 packet, delivered with 5.b because the owner asked for Checks in this phase.

Fourteen scenarios, `CH-WRITE-001` … `CH-DELETE-014`, in a new
[`tests/brd/checks.feature`](../../tests/brd/checks.feature).

## What the owner changed in review, and what it cost

**A Check hangs on one Card, or on none — never on several.** That was the review's largest ruling
and it deletes machinery rather than adding it: the "live Cards in the series" walk, the shared-Check
survival rule on archive and delete, and the "answer settles it on every Card" case all had one
purpose, which was to hold a Check that several Cards disagreed about. Nothing disagrees now.

| First draft | Now |
|---|---|
| One Check may hang on many Cards | Gone. One Card or none, and a Check keeps its many Values |
| One answer settles it on every Card it hangs on | Gone. Answering again just overwrites the answer |
| A successor is linked to whichever Cards are still live | The one Card, if it is live |
| A series ends when no live Card is left | Closing the Card deletes the open instance; nothing else ends a series |
| Archiving a Card takes its Checks, unless another needs them | Gone. Each leaves on its own clock — 5.d |
| A Check is never deleted on its own | Gone. The owner ruled it can be. CH-DELETE-014 |
| Written by Safwa, answered by the owner | Answered by hand **or** by a proposal the owner saved |

## Three rules generate both matrices

The owner set out eight cases: four ways of closing a Card that carries a Check, and four ways of
reopening one. They are not eight rules; they are three, and the tables are what those three produce.
Eight rules would need eight code paths, and they would drift apart.

- **R1 — a Card closes when every Check series on it was answered at least once, on this Card.**
- **R2 — closing a Card deletes whatever is still Pending on it; a repeat successor is given one
  fresh Pending instance per series.**
- **R3 — reopening a Card puts each plain Check on it back to Pending, and opens one fresh Pending
  instance of each repeating series.**

Closing:

| Card | Check | Can it close? | What is left |
|---|---|---|---|
| plain | plain | only once answered (R1) | nothing Pending (R2) |
| plain | repeating | only once answered at least once (R1) | the Pending instance is deleted (R2) |
| repeating | plain | only once answered (R1) | the successor Action carries a fresh unanswered copy (R2) |
| repeating | repeating | only once answered at least once (R1) | the Pending instance is deleted here and opens on the successor (R2) |

Reopening:

| Card | Check | Can it reopen? | What it gets back |
|---|---|---|---|
| plain | plain | yes | the same Check, Pending again, its answer wiped (R3) |
| plain | repeating | yes | one fresh Pending instance of the series (R3) |
| repeating | plain | no — CD-STAGE-017 | — |
| repeating | repeating | no — CD-STAGE-017 | — |

Cancelling is not gated: R1 is about Done. R2 and R3 apply whichever way the Card closed.

The asymmetry between the two halves of R3 is the point. A plain Check is *the* observation of that
Card, so reopening the work reopens the question that was asked about it. A repeating Check's answer
belongs to a cycle that is over, so the reopened Card asks the next one instead of unsaying the last.

## Scope

In: what a Check is, who writes one and who answers it, the one Card it may hang on, Pending,
answering and answering again, the gate it puts on finishing, the repeat series, and what closing
and reopening a Card do to it.

Out, and why:

- **The Values a Check carries.** Approved in Phase 4.d as `VL-CHECK-010` … `VL-CHECK-012`, including
  that an answered repeat hands its Values to its successor. `VL-CHECK-011` survives the archive
  ruling unchanged; the rest of that file does not — see 5.d.
- **When a Check leaves the workspace.** CH-ARCHIVE-013 and CH-DELETE-014 state it; the rule they
  state belongs to [archive_and_delete.md](archive_and_delete.md), packet 5.d.
- **The closed-repeat marker, and that no tool may touch a closed repeat.** One rule over a Card and
  a Check both. Packet 5.d.
- **The Check screens as code.** Phase 8 moves the handler package whole.

---

## Scenarios

### CH-WRITE-001 — A Check is an observation, not a task

Status: draft
Sources: INITIAL_PLAN §"A Check records a state observation, not planned work. It has no effort and
never enters a Sprint"; `CheckToolInput`

```gherkin
Given the owner wants to record whether something held
Then a Check carries a title and whether it repeats, and nothing else
And it has no effort, no stage, no priority, and never counts towards a Sprint
```

### CH-WRITE-002 — Safwa writes a Check, and either of them answers it

Status: draft. Revised: the first draft said only the owner answers.
Sources: INITIAL_PLAN §"Creating one, renaming it, and linking or unlinking it from a Card happen
only through an AI proposal"; the owner's ruling of 2026-08-24; `CheckToolInput`

```gherkin
Given a Check that exists
Then writing one, renaming one and linking one to a Card are proposals the owner saved
And its own screen answers it and turns repeat on or off, and offers nothing else
And Safwa may propose the answer too, and it reaches the owner as a screen to save
And a Check title that is empty, or nothing but spaces, is refused
```

### CH-LINK-003 — A Check belongs to one Card, or to none

Status: draft. New behaviour: several Cards may share a Check today.
Sources: the owner's ruling of 2026-08-24; INITIAL_PLAN §"A Card is a Check's only relationship"

```gherkin
Given a Check "Posture straight?"
When it is linked to a Card
Then linking it to a second Card is refused
And moving it to another Card is done by taking it off the first one
And a Check linked to no Card is allowed
And it carries as many Values as the owner likes
```

### CH-ANSWER-004 — A Check with no answer is Pending

Status: draft
Sources: INITIAL_PLAN §"Pending is derived from an unanswered Check and is never stored";
`checks.outcome IS NULL`; `ai_checks.status`

```gherkin
Given a new Check
Then it reads as Pending everywhere: on the Card, on its own screen, and to Safwa
And nothing was stored to say so
And nothing puts an answered Check back to Pending except reopening the Card it is on
```

### CH-ANSWER-005 — Answering a Check again only changes the answer

Status: draft. Revised: the first draft carried the many-Cards case.
Sources: INITIAL_PLAN §"A resolved Check may be re-answered; the previous outcome is overwritten and
not retained"; `_apply_check_outcome`; the owner's ruling of 2026-08-24

```gherkin
Given a Check the owner answered Passed
When the owner opens it and answers Missed instead
Then it is Missed, and the earlier answer is gone rather than kept beside it
And the time the observation was made does not move
And no new instance opens, and the Card it is on is untouched
```

### CH-GATE-006 — A Card cannot be Done with an unanswered Check

Status: draft
Sources: INITIAL_PLAN §"A Card cannot be completed while it has Pending Checks. Cancelling is not
gated"; `finish_action`

```gherkin
Given an Action carrying a Check that has never been answered
When the owner finishes it as Done
Then it is refused, and the refusal names the Check that is still unanswered
And nothing about the Action changed
When the owner cancels the same Action instead
Then it is cancelled with the Check left unanswered
```

### CH-GATE-007 — Finishing a Card answers its Checks at the same time

Status: draft. Revised: the atomicity line is gone; CH-GATE-006 already covers the partial answer.
Sources: `finish_action`, `_pending_check_resolutions`

```gherkin
Given an Action carrying two Pending Checks
When the owner finishes it and answers both in the same act
Then the Action is Done and both Checks carry their answers
```

### CH-GATE-008 — A repeating Check needs one answer before its Card can close

Status: draft. New behaviour: a Pending repeat blocks Done today, on any Card.
Sources: the owner's ruling of 2026-08-24; rule R1 above

```gherkin
Given an Action carrying a repeating Check that was answered once, so the next one is Pending
When the owner finishes the Action as Done
Then it is allowed, and the Pending instance does not hold it
Given instead an Action whose repeating Check has never been answered on it
When the owner finishes it as Done
Then it is refused, and the refusal names that Check
And an answer given on the Action this one repeated from does not count
```

### CH-REPEAT-009 — Answering a repeating Check opens the next one

Status: draft
Sources: INITIAL_PLAN §"A repeatable Check produces a fresh Pending successor as soon as it is
answered"; `_spawn_check_successor`

```gherkin
Given a repeating Check on a live Card
When the owner answers it
Then the answered one keeps its answer and stays where it is
And a fresh Pending instance opens on that same Card, with the same title, still repeating
And answering the answered one again opens no second instance
And a repeating Check on no Card opens the next one just the same
```

### CH-CLOSE-010 — Closing a Card deletes its unanswered Check

Status: draft. New behaviour: the Pending instance is left in place today.
Sources: the owner's ruling of 2026-08-24; rule R2 above

```gherkin
Given an Action that does not repeat, carrying a repeating Check answered once, so one is Pending
When the owner finishes the Action
Then the Action closes and the Pending instance is deleted outright
And the answered ones stay on it
```

### CH-CLOSE-011 — A repeating Card gives fresh Checks to the next one

Status: draft
Sources: INITIAL_PLAN §"Repeat successor Cards carry one Pending copy of each of their Card's Check
series"; `_clone_checks_for_successor`; rule R2 above

```gherkin
Given a repeating Action carrying a plain Check and a repeating Check, both answered
When the owner finishes the Action
Then a successor Action is made, and each series lands on it as exactly one Pending instance
And the plain Check is written fresh there too, unanswered
And the answered instances stay with the Action that closed
And the successor cannot be finished until each of them has been answered once, by CH-GATE-008
```

### CH-REOPEN-012 — Reopening a Card brings its Checks back

Status: draft. New behaviour: reopening leaves every Check answered today.
Sources: the owner's ruling of 2026-08-24; rule R3 above; `move_card` (the terminal branch)

```gherkin
Given an Action that does not repeat, closed on a plain Check and on a repeating Check
When the owner reopens it
Then the plain Check is that same Check, Pending again, with its answer and its answer time wiped
And the repeating series opens a fresh Pending instance on the Card, leaving its answers alone
And the Action cannot be finished again until both have been answered again
Given instead an Action that repeats
When anything tries to reopen it
Then it is refused by CD-STAGE-017, and no Check on it changes
```

### CH-ARCHIVE-013 — An answered Check is archived two Sprints later

Status: draft, and it states a rule owned by 5.d. New behaviour throughout.
Sources: the owner's ruling of 2026-08-24; [archive_and_delete.md](archive_and_delete.md)

```gherkin
Given a Check that was answered
When two Sprints have gone by since it was answered
Then it is archived without anyone asking, off the screens by default and still in every count
And its Card did not take it there, and it did not wait for its Card
And the owner may archive an answered Check by hand before then
And a Check that is still Pending cannot be archived
```

### CH-DELETE-014 — Deleting a Check deletes it

Status: draft. New behaviour: `remove(mode="delete")` refuses anything but a Card today.
Sources: the owner's ruling of 2026-08-24; [archive_and_delete.md](archive_and_delete.md)

```gherkin
Given a Check the owner wants gone
When the owner or Safwa asks to delete it
Then the Check is gone, answered or not, and it is never archived instead
And its Card stays, and so do the Values it pointed at
```

---

## What the owner settled

**Nothing is in production, so shared Checks are not a case.** `CardCheck` takes a unique constraint
on `check_id` and the owner rebuilds the database. No migration is written for rows that only exist
in tests.

**A closed repeating Action keeps its answered instances and loses the Pending one.** They are the
record of the cycle that Action did, and the observation that let it close under R1. CH-CLOSE-010 and
CH-CLOSE-011 are written for it.

---

## Audit of the existing tests

| Scenario | Existing tests | Class | Decision | New tests |
|---|---|---|---|---|
| CH-WRITE-001 | — | missing | — | one, on the contract and the model |
| CH-WRITE-002 | `test_checks_e2e.py::test_manual_check_screens_only_repeat_and_answer` | business_valid | rename, cite | one for the empty title, one for the proposed answer |
| CH-LINK-003 | `test_checks.py::test_one_check_serves_several_cards` | **business_invalid** | delete: it asserts the rule the owner removed | one for the refusal, one for the Card-less Check |
| CH-ANSWER-004 | `test_checks.py::test_new_check_is_pending_and_starts_its_own_series` | business_valid | rename, cite | — |
| CH-ANSWER-005 | `test_checks.py::test_repeatable_check_spawns_one_successor_and_re_answer_does_not` (partly) | business_valid | split, cite | one for the untouched observation time |
| CH-GATE-006 | `test_checks.py::test_done_is_gated_on_pending_checks_and_cancel_is_not`, `test_checks_e2e.py::test_completion_is_refused_while_checks_are_pending` | business_valid | rename both, cite | — |
| CH-GATE-007 | `test_checks.py::test_finishing_resolves_checks_and_leaves_the_card_done`, `::test_partial_or_unknown_check_outcomes_are_rejected` | business_valid | rename both, cite | — |
| CH-GATE-008 | — | missing, and the behaviour does not exist | — | written first, failing first, both branches |
| CH-REPEAT-009 | `test_checks.py::test_only_one_pending_check_per_series`, `::test_standalone_check_repeats_without_a_card` | business_valid | rename both, cite | — |
| CH-CLOSE-010 | — | missing, and the behaviour does not exist | — | written first, failing first |
| CH-CLOSE-011 | `test_checks.py::test_repeat_successor_card_gets_pending_check_copies_with_flags_intact`, `::test_repeat_successor_copies_one_row_per_series` | business_valid | rename both, cite | one for the answered instances staying |
| CH-REOPEN-012 | — | missing, and the behaviour does not exist | — | written first, failing first, one per half |
| CH-ARCHIVE-013 | `test_checks.py::test_archive_and_delete_keep_a_check_its_other_cards_still_need` | **business_invalid** | delete: it is the shared-Check cascade | one for the clock, one for the Pending refusal |
| CH-DELETE-014 | — | missing, and the behaviour does not exist | — | written first, failing first |

`test_checks.py::test_successor_is_linked_to_live_cards_only` and
`::test_terminal_card_never_regains_a_pending_check` are **business_invalid** and are deleted: both
assert the eligible-Cards walk, and R2 deletes the open instance at close instead of declining to
make one.

`test_checks.py::test_a_resolved_repeatable_check_names_the_newest_open_one` and
`test_checks_e2e.py::test_a_closed_repeat_is_marked_everywhere_it_is_read` stay green and uncited
until 5.d, which owns the closed-repeat marker. The citation, proposal and manual-screen tests in
`test_checks_e2e.py` keep their own homes: they are the proposal and citation paths, not Check rules.

Two tests carry two rules each and are split rather than cited twice — the re-answer test and the
successor test. A failure that names one scenario has to be about that scenario.

---

## What the code batch does after approval

- `CardCheck` gains a unique constraint on `check_id`, and `toggle_card_check` refuses the second
  Card. That is a schema change, so the batch declares the `schema.json` hash and the owner rebuilds
  the database.
- `_spawn_check_successor` loses its eligible-Cards walk: one Card, live or not. `archive_subtree`
  and `delete_subtree` lose `_linked_checks` and `_has_other_live_card` entirely — 5.d takes what is
  left of them.
- R2 is one line in `finish_action`: the Pending instances it did not answer are deleted, which is
  the branch `spawn=False` exists to avoid today and can then go.
- R1 replaces the flat `pending_checks` gate: a series counts as observed when any instance of it on
  this Card has an outcome.
- R3 is new code and the only place a Check goes backwards: `outcome`, `resolved_at` and
  `resolved_by` are cleared on each plain Check, and `_copy_check` opens one instance per repeating
  series. `move_card`'s reopen branch calls it through `features/checks/api.py` rather than reaching
  into the rows itself, the same door `finish_action` uses.
- `RemoveToolInput` lets `delete` through for a Check, and the Check screen grows the control.
- `features/checks/model.py` takes `Check`; `CardCheck` stays in `features/cards/model.py`, because
  the link is written from the Card and recorded in the Card's history.
- `features/checks/use_cases.py` takes `create_check`, `update_check_fields`, `archive_check`,
  `delete_check`, `resolve_check`, the successor machinery, and the readers `card_checks` and
  `pending_checks`.
- `features/checks/api.py` is the door Cards needs: finishing an Action asks which series are
  unobserved and hands back the answers, and the repeat successor asks for one copy per series. Cards
  must not reach into the Check model to do either.
- `CheckOutcome`, `CHECK_OUTCOME_LABELS` and `CHECK_ANSWER_ACTIONS` move out of `enums.py` into the
  feature, the way `CardStage` and its two sets move in 5.b.
- `domain.py` is expected to fall by roughly 250 lines.
