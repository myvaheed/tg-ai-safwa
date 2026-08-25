# Cards: stages, and what a Goal shows — approval packet

Status: **approved** 2026-08-24. Scenarios preserved in `tests/brd/cards.feature`.
Batch: Phase 5.b
Sources: `archived_docs/INITIAL_PLAN.md` §Stages, `CLAUDE.md` §Domain invariants, `move_card`,
`finish_action`, `propagate_ancestors`, `aggregate_child_stages`, `telegram/cards.py`, and the tests
in the audit.

The second Phase 5 packet. 5.a said what a Card is; this one says where it sits on the ladder, who
is allowed to say so, and what a Goal or an Idea shows for the Actions underneath it.

Nine scenarios, `CD-STAGE-013` … `CD-EFFORT-021`, continuing the Cards numbering.
The approved ones join [`tests/brd/cards.feature`](../../tests/brd/cards.feature).

## What the owner changed in review

**A stage is an Action's field, exactly like effort and Blocked.** A Goal and an Idea do not have
one; they show what their branch has got to.

| First draft | Now |
|---|---|
| A parent derives its stage from its children | Kept, and it is the *only* way a parent has a stage |
| Moving a Card moves everything under it | Gone. Only an Action moves, and an Action has no children |
| A change climbs to the top and reports what moved | Gone. It was CD-STAGE-014 said a second way |
| A populated Goal is closed by closing its children | *Every* Goal is closed that way |
| A Goal shows the block of its branch, and is not blocked | Split: CD-BLOCKED-018 forbids, CD-BLOCKED-019 shows |
| A Goal with nothing under it | Shows Backlog, and never Done or Cancelled |
| Effort was out of scope | In: a Goal shows the total of its branch, CD-EFFORT-021 |

## Scope

In: who may set a stage; what a Goal and an Idea show instead; which door reaches Done and
Cancelled; reopening; and Blocked and effort — refused on a parent, shown for one.

Out, and why:

- **Checks as the Done gate.** Packet 5.c, delivered with this one.
- **Archiving and deleting.** Packet 5.d, delivered with this one.
- **The repeat successor Action.** Packet 5.c owns what its Checks do; 5.d owns the closed-repeat
  marker. Only the *refusal to reopen* one is here, in CD-STAGE-017, because that is a stage rule.
- **The Sprint and its commitments.** Packet 5.e. `sync_commitment_for_stage` is a Sprint reaction to
  the moves described here.
- **Card progress and the dashboards.** Packet 5.f.

---

## Scenarios

### CD-STAGE-013 — Only an Action has a stage

Status: draft. New behaviour: a Goal and an Idea can be moved today.
Sources: the owner's ruling of 2026-08-24; `CD-FIELD-007`, which is this rule for the other fields

```gherkin
Given a Goal, an Idea under it, and an Action under the Idea
When the owner moves the Action to Today
Then it is in Today
When anything tries to move the Goal or the Idea
Then it is refused before anything is written, in a proposal and on a screen alike
And no screen offers a Goal or an Idea a stage control
```

### CD-STAGE-014 — A Goal shows the stage of the Actions under it

Status: draft
Sources: INITIAL_PLAN §"Populated Goal and Idea stages are derived from descendants: Today, then
Sprint, then Backlog"; `aggregate_child_stages`, `propagate_ancestors`

```gherkin
Given a Goal with one Action in Backlog and one in Sprint
Then the Goal shows Sprint
When one of them moves to Today
Then the Goal shows Today, and so does every Card between them
And Today wins over Sprint, and Sprint wins over Backlog
And an Action anywhere in the branch counts, not the direct children only
And a Goal with no Action anywhere under it shows Backlog
```

### CD-STAGE-015 — A Goal is Done only when everything under it is finished

Status: draft
Sources: INITIAL_PLAN §"all-terminal descendants produce Done when at least one is Done, otherwise
Cancelled"; `aggregate_child_stages`

```gherkin
Given a Goal whose children have all been finished
When at least one of them is Done
Then the Goal shows Done
When every one of them was Cancelled
Then the Goal shows Cancelled
And a Goal with one Done Action and one live Action shows the live one's stage
And an Idea with nothing in it holds the Goal above it in Backlog
And a Goal with nothing under it never shows Done or Cancelled
```

### CD-STAGE-016 — Done and Cancelled come from finishing, not from moving

Status: draft
Sources: `move_card`; `CardToolInput` (`move` refuses a terminal stage); INITIAL_PLAN §Checks gate

```gherkin
Given an Action in Today
When anything tries to move it straight to Done or Cancelled
Then it is refused, in a proposal and on a screen alike
And finishing it settles its Checks, its Sprint result and its repeat in the same act
```

### CD-STAGE-017 — Reopening an Action undoes what closing it did

Status: draft
Sources: `move_card` (the terminal branch and `is_closed_repeat`);
`test_reopening_a_finished_action_clears_its_sprint_result`

```gherkin
Given an Action the owner finished as Done, so it has a completion time
When the owner reopens it
Then the completion and cancellation times are cleared
And the Checks it was closed on are given back to it, by CH-REOPEN-012
And the Goal above it shows a live stage again
Given instead an Action that repeats and has been finished
When anything tries to reopen it
Then it is refused
```

### CD-BLOCKED-018 — Only an Action can be marked blocked

Status: draft. New behaviour: the refusal was added in 5.a; the emphasis here is the owner's.
Sources: the owner's ruling of 2026-08-23 recorded in [cards.md](cards.md) §D2; `CD-FIELD-007`

```gherkin
Given a Goal, an Idea and an Action
When the Action is marked blocked, with a reason
Then it is blocked, and the reason is the words that were given
When anything tries to mark the Goal or the Idea blocked
Then it is refused before anything is written, in a proposal and on a screen alike
And no screen offers a Goal or an Idea a Blocked control
```

### CD-BLOCKED-019 — A Goal shows the blocked Actions under it

Status: draft. New behaviour: nothing shows a block above the Action today.
Sources: the owner's ruling of 2026-08-23 and of 2026-08-24; CD-BLOCKED-018

```gherkin
Given a Goal with a blocked Action somewhere underneath it
Then the Goal reads as blocked, on its screen and to Safwa alike
And its screen names each blocked Action and quotes the reason that Action gave
And the Goal itself has no reason of its own
When the Action is unblocked, finished, archived or deleted
Then the Goal stops reading as blocked
```

### CD-BLOCKED-020 — Being blocked does not stop anything

Status: draft
Sources: `move_card` and `finish_action` (`result.warnings`);
`test_moving_a_blocked_card_shows_its_warning_on_the_card_screen`

```gherkin
Given a blocked Action in Today
When the owner moves it, or finishes it
Then it moves and it finishes
And the reason is on the screen the owner lands on, not in a message the next screen overwrites
```

### CD-EFFORT-021 — A Goal shows the effort of the Actions under it

Status: draft. New behaviour: a Goal and an Idea hold no effort at all today.
Sources: the owner's ruling of 2026-08-24; `card_progress`; `CD-FIELD-007`

```gherkin
Given a Goal with three Actions of 5 points under it
Then the Goal shows 15
When one of them changes to 8
Then the Goal shows 18
And an Idea between them shows the total of its own branch
And a Goal and an Idea still refuse an effort set by hand
And a Goal with no Action under it shows no effort
```

---

## What the owner settled on the second review

**An empty Goal shows Backlog.** A branch with no Action in it has nothing to take a stage from, so
the bottom of the ladder is what it reads. Such a Goal never shows Done or Cancelled: to be rid of
one, the owner deletes it. `manual_stage` on a Goal and an Idea stops being written and read.

**A derived value is stored in the plain column, not computed in the view.** The owner's call, and
it is the cheaper one on every count that matters here:

- Safwa reads one column, `blocked`, and it means the same thing on every row. A second column, or a
  `CASE` in the view, is one more thing a 4B model has to get right in a query it writes itself.
- Nothing is recomputed on read. The walk runs once, where the change happened.
- It is already how stage works: `effective_stage` is a real column that `propagate_ancestors`
  writes on every move, and `ai_cards` hands it over as `stage`. Blocked joins it.

The price, stated plainly: a stored derived value can go stale, and a stale one is a lie the model
will read out loud. Every path that changes an Action has to call the walk. That is already true of
stage, it has held, and the same walk carries blocked — one function, not two.

**A Goal has no reason of its own.** `blocked` on a parent is the flag and nothing more;
`blocked_description` stays empty there, because several blocked Actions have several reasons and
picking one, or splicing them, would be Safwa writing the owner's words. The screen lists the
Actions and quotes each of them; Safwa reads the flag and can ask which Actions they are.

**The branch total goes into `effort_points` itself.** No second column: a Goal and an Idea cannot be
given an effort of their own, so the column is free to hold the one number they do have. It replaces
`card_progress`'s effort half, which computes the same total for the screens today and is invisible
to a query.

One consequence to know, because it will show up in an answer rather than in an error: a query that
adds up `effort_points` over every row counts each Action once for itself and once inside each
ancestor, so three Actions of 5 under one Goal add up to 20. Anything that wants the real total adds
up Actions — `WHERE kind = 'action'`. The prompt's `ai_cards` line says so in those words, which is
the one place it has to be said.

---

## Audit of the existing tests

| Scenario | Existing tests | Class | Decision | New tests |
|---|---|---|---|---|
| CD-STAGE-013 | — | missing, and the refusal does not exist | — | written first, failing first |
| CD-STAGE-014 | `test_domain.py::test_parent_stage_propagation_and_reopen` (partly) | business_valid | split, cite | one for the whole-branch walk, one for the empty Goal |
| CD-STAGE-015 | — | missing | — | one, both branches |
| CD-STAGE-016 | `test_domain.py::test_an_action_cannot_reach_a_terminal_stage_through_move`, `test_card_creation.py::test_card_move_tool_rejects_a_terminal_stage` | business_valid | rename both, cite | — |
| CD-STAGE-017 | `test_domain.py::test_reopening_a_finished_action_clears_its_sprint_result` | business_valid | keep; its Sprint half is 5.e's | one for the closed repeat; the Checks half is CH-REOPEN-012's |
| CD-BLOCKED-018 | `test_cards.py::test_only_an_action_carries_effort_repeat_and_blocked` (CD-FIELD-007) | business_valid | keep where it is | one for the proposal path |
| CD-BLOCKED-019 | — | missing, and the behaviour does not exist | — | written first, failing first |
| CD-BLOCKED-020 | `test_telegram_item_ui.py::test_moving_a_blocked_card_shows_its_warning_on_the_card_screen` | business_valid | rename, cite | one for finishing |
| CD-EFFORT-021 | `test_domain.py` card-progress tests (partly) | business_valid | keep for 5.f; the totals move here | written first, failing first |

**Tests the ruling breaks.** Anything that moves a Goal or an Idea stops being a business rule the
moment CD-STAGE-013 lands. The batch finds them by running the suite against the refusal, not by
reading — the ones already known are the parent-move halves of
`test_parent_stage_propagation_and_reopen` and the manual-screen fixtures that build a Goal in
Sprint. Each is rewritten to put the Action where the Goal used to be put.

`test_quick_move_buttons_walk_an_action_between_today_and_sprint` and
`test_backlog_dashboard_lists_actions_only` stay green and uncited: the first is the Sprint's quick
controls (5.e), the second a dashboard projection (5.f).

---

## What the code batch does after approval

- `move_card` loses its subtree walk. An Action has no children and no other kind may be moved, so
  the recursion has nothing to visit; what remains is one Card, its event, its Sprint commitment, and
  the climb.
- The stage control is refused for a Goal and an Idea at both boundaries, `CardToolInput` and
  `telegram/cards.py`, the way `blocked` and `effort_points` already are.
- `propagate_ancestors` becomes the one walk that writes every derived value — stage today, blocked
  and effort with it. One pass, one place to call, and every path that changes an Action calls it.
- No column is added: `blocked` and `effort_points` on a parent are the ones already there. `ai_cards`
  keeps its shape and gains no `CASE`; only the `effort_points` wording in the prompt changes, which
  moves `SYSTEM_PROMPT` and `BOARD_PROMPT`.
- `manual_stage` stops being written and read for a Goal and an Idea.
- **Completion feedback is torn out in this batch, to be built again from scratch later.** It has no
  scenario in any packet and none is written for it: `FeedbackQueue`, `Card.liked`, `set_feedback`,
  the `/feedback` command and its screen, the `feedback` callback, the pending count on `/status`,
  and the `liked` inputs to `analytics.py` and its capacity advice all go. `move_card`'s reopen
  branch loses the two lines that clear them, which is why the removal rides here.
- `move_card`, `finish_action` and the reopen branch move to `features/cards/use_cases.py`, next to
  `propagate_ancestors`, which went there in 5.a.
- `TERMINAL_STAGES`, `LIVE_STAGE_PRECEDENCE` and `CardStage` move into the Cards feature together —
  5.a left them in `enums.py` on purpose, because a set split from the enum it enumerates is worse
  than one that waits. This is the batch the waiting was for.
- `domain.py` is expected to fall by roughly 200 more lines.
