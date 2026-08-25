# Archiving and deleting — approval packet

Status: **approved** 2026-08-24. Scenarios preserved in `tests/brd/cards.feature`,
`values.feature`, `tags.feature` and `saved_requests.feature`.
Batch: Phase 5.d
Sources: the owner's ruling of 2026-08-24; `archive_subtree`, `delete_subtree`, `archive_check`,
`archive_value`, `archive_tag`, `archive_saved_request`, `RemoveToolInput`;
[MIGRATION.md](../MIGRATION.md#what-can-be-deleted-and-what-can-only-be-archived).

Three phases have written packets around a sentence that was never settled, and the confusion is
that two exits existed and neither had a reason. This packet gives archiving one reason and takes it
away from everything that does not have that reason.

Seven scenarios here, plus two that live in packet 5.c because they are Check rules:
`CH-ARCHIVE-013` and `CH-DELETE-014`.

This document spans four packages, so its scenarios land in four `.feature` files — `cards`,
`values`, `tags`, `saved_requests`. The rule is one and the review is one; the files stay one per
package, as they are.

## The rule

**Archiving exists so the workspace does not fill up.** Nothing else. It follows that a thing may be
archived only when both are true of it:

1. the owner makes **many** of them, and
2. it reaches a **state where it is over**.

Only two things in Safwa are both: a **Card** and a **Check**. They go to the archive **on their
own**, two Sprints after they closed. Archived is a matter of sight and nothing else — they still
count in effort, in completed Cards, in every trend that ever counted them.

**Everything else is deleted.** A Value, a Tag, a Saved Request: the owner has a handful, they never
finish, and there is nothing to declutter. Deleting breaks the links and leaves the things they were
on standing — a Card that loses a Value is still that Card.

**Delete means delete.** When the owner asks for something to be deleted, it is deleted, Card and
Check included. Archiving is never quietly substituted for it.

## What this supersedes

Approved scenarios that state the old rule. Each is rewritten by this packet, and no code moves until
the owner approves it here.

| Approved scenario | What it said | What replaces it |
|---|---|---|
| `VL-ARCHIVE-007`, `VL-ARCHIVE-008` | A Value is archived and never deleted | `VL-DELETE-015` |
| `VL-NAME-006` | Writing a Value's name brings the archived one back | Deleted: the name is simply free |
| `VL-LINK-009` | Nothing can be linked to an archived Value | Deleted: there is no archived Value |
| `VL-FOCUS-002`, `VL-READ-003` | one line each about an archived Value | The line goes; the rest stands |
| `TA-ARCHIVE-004`, `TA-ARCHIVE-005` | A Tag is archived and never deleted | `TA-DELETE-008` |
| `TA-NAME-003`, `TA-LINK-006` | Revive by name; no linking an archived Tag | Deleted, same reason as the Values pair |
| `SR-AI-009` | A Request is archived, never deleted | `SR-DELETE-013` |
| `SR-WRITE-003` | Saving under an archived Request's name brings it back | Deleted: the name is simply free |
| `SR-READ-011`, `SR-UI-012` | one line each about an archived Request | The line goes; the rest stands |
| `CH-ARCHIVE-013` *(first draft)* | Archiving a Card takes its Checks with it | `CH-ARCHIVE-013` as rewritten in 5.c |

`VL-CHECK-011` — archiving a Check keeps its Values, deleting one takes them away — stands unchanged.
A Check keeps both exits, so both halves of it are still true. `CD-TREE-006` stands too: an archived
Card is still not a parent.

## Scope

Out, and why:

- **The closed-repeat marker** ` [🔄3]` and the rule that no tool may touch a closed repeat. It rides
  with this packet in code, because `CD-ARCHIVE-024` is where the owner's "a closed repeat never
  comes back" lands, but the marker itself is a rendering rule and gets its own scenario in 5.f.
- **What the Sprint is.** Packet 5.e. This packet uses Sprint endings as a clock and says nothing
  else about them.

---

## Scenarios

### CD-ARCHIVE-022 — A closed Card is archived two Sprints later

Status: draft. New behaviour: archiving is only ever done by hand today.
Sources: the owner's ruling of 2026-08-24

```gherkin
Given an Action that was finished during the Sprint that has just ended
When two Sprints have ended since the one it closed in
Then it is archived without anyone asking
And a Card still open is never archived, however old it is
And a Goal whose branch is not finished is not archived either
```

### CD-ARCHIVE-023 — An archived Card is hidden but still counted

Status: draft
Sources: the owner's ruling of 2026-08-24; `card_progress`, the Sprint result readers

```gherkin
Given a Goal with two finished Actions under it, one of them archived
Then the screens, the dashboards and Safwa show the live one and not the archived one
And the effort of both is still counted, and both still count as Cards that were completed
And the Sprint it closed in still reports it
```

### CD-ARCHIVE-024 — Only a closed Card can be archived by hand

Status: draft. New behaviour: nothing gates archiving today, and an archived Card cannot be reopened
at all — `move_card` refuses one before it looks at the stage.
Sources: the owner's ruling of 2026-08-24; `is_closed_repeat`; `move_card`

```gherkin
Given a live Action
When the owner or Safwa asks for it to be archived
Then it is refused: only a Card that is Done or Cancelled may be archived
Given instead an Action that is Done
When the owner archives it
Then it is archived at once, without waiting for the two Sprints
When the owner reopens an archived Card
Then it is out of the archive and back on the screens
And an archived Action that repeats stays archived: it cannot be reopened
```

### CD-DELETE-025 — Deleting a Card deletes everything under it

Status: draft
Sources: `delete_subtree`; the owner's ruling of 2026-08-24

```gherkin
Given a Goal with Ideas and Actions under it
When the owner or Safwa asks for the Goal to be deleted
Then the Goal and everything under it is gone, open or closed, archived or not
And a Check that was on them is deleted with them, answered or Pending
And a Check that carries a Value stays instead, with no Card
And the Values and Tags they carried lose the link and nothing else
And their Sprint commitments and their history go too
And archiving is never substituted for it
```

### VL-DELETE-015 — A Value is deleted, not archived

Status: draft. New behaviour: a Value is archived and never deleted today.
Sources: the owner's ruling of 2026-08-24; supersedes `VL-ARCHIVE-007`, `VL-ARCHIVE-008`

```gherkin
Given a Value carried by Cards and by Checks
When the owner or Safwa asks for it to be removed
Then it is deleted, and every Card and Check that carried it loses that link and nothing else
And no screen offers to archive a Value, and the remove tool refuses to
And its name is free from that moment, and a new Value taking it is a new Value
```

### TA-DELETE-008 — A Tag is deleted, not archived

Status: draft. New behaviour: a Tag is archived and never deleted today.
Sources: the owner's ruling of 2026-08-24; supersedes `TA-ARCHIVE-004`, `TA-ARCHIVE-005`

```gherkin
Given a Tag on several Cards
When the owner or Safwa asks for it to be removed
Then it is deleted, and those Cards lose the label and are otherwise untouched
And no screen offers to archive a Tag, and the remove tool refuses to
And its name is free from that moment
```

### SR-DELETE-013 — A Request is deleted, not archived

Status: draft. New behaviour: a Request is archived and never deleted today.
Sources: the owner's ruling of 2026-08-24; supersedes `SR-AI-009`, `SR-WRITE-003`

```gherkin
Given a saved Request that a Plan filter is using
When the owner or Safwa asks for it to be removed
Then it is deleted, and the filter stops using it without an error anywhere
And no screen offers to archive a Request, and the remove tool refuses to
And its name is free from that moment
```

---

## What the owner settled

**The clock is Sprint endings.** At the end of every Sprint, everything that closed before the Sprint
before it is archived. Nothing is archived while the workspace is in Planning, and the backlog of it
clears the moment the next Sprint ends. One place in the code, firing on an event that already
exists.

**`archived_at` comes off Value, Tag and Saved Request**, and the archive branch of `remove` goes
with it. The database is rebuilt before release, so it costs a `schema.json` declaration and nothing
else.

**Deleting a Card deletes its Checks, except the ones that carry a Value.** A Check with no Value is
a question about that Card and nothing else, so it goes with it. A Check that carries a Value is also
a measurement of that Value, and 5.c makes a Card-less Check a normal thing to be, so it stays and
keeps measuring. CD-DELETE-025 is written for it.

---

## Audit of the existing tests

| Scenario | Existing tests | Class | Decision | New tests |
|---|---|---|---|---|
| CD-ARCHIVE-022 | — | missing, and the behaviour does not exist | — | written first, failing first |
| CD-ARCHIVE-023 | — | missing | — | one, on the counts |
| CD-ARCHIVE-024 | `test_domain.py::test_archive_subtree_walks_children` (partly) | business_valid | split, cite | one for the refusal on a live Card, one for the closed repeat |
| CD-DELETE-025 | `test_domain.py::test_delete_subtree_removes_children` | business_valid | rename, cite | one for the untouched Values and Tags |
| VL-DELETE-015 | `test_values.py` archive tests | **business_invalid** | delete them: they assert the rule the owner removed | one, and one for the freed name |
| TA-DELETE-008 | `test_tags.py` archive tests | **business_invalid** | delete them, same reason | one, and one for the freed name |
| SR-DELETE-013 | `test_saved_requests.py` archive tests | **business_invalid** | delete them, same reason | one, and one for the freed name |

The exact test names are left to the batch: the packet that renames them is the packet that runs the
suite against the new refusals, and listing them here from a grep would be listing them twice.

---

## What the code batch does after approval

- `RemoveToolInput` is turned inside out: `delete` is the default and is allowed for every entity,
  `archive` is allowed for a Card and a Check alone and refused when the target is not closed. The
  tool description changes, so the batch declares the `SYSTEM_PROMPT` and `BOARD_PROMPT` hashes.
- `archive_value`, `archive_tag` and `archive_saved_request` become `delete_value`, `delete_tag` and
  `delete_saved_request`, each deleting its link rows explicitly rather than trusting the FK cascade,
  the way `delete_subtree` already does.
- `delete_subtree`'s Check pass changes shape: instead of asking whether another Card still needs a
  Check, it asks whether that Check carries a Value. `_has_other_live_card` goes; a `CheckValue`
  probe takes its place.
- Every `archived_at IS NULL` on those three drops out of `ai/context.py`, the `ai_*` views, the
  screens and the pickers. That moves the `ai_values`, `ai_tags` and `ai_requests` shapes, so the
  batch declares the `schema.json` hash with them.
- The archiver runs where a Sprint ends and archives Cards and Checks in one pass over each. The
  Value, Tag and Request columns it no longer needs are dropped in the same batch.
- `archive_subtree` keeps the walk and loses `_linked_checks` and `_has_other_live_card` — a Check
  now leaves on its own clock, and 5.c already takes the shared-Check machinery out.
- `MIGRATION.md`'s "What can be deleted, and what can only be archived" is rewritten to this rule,
  and the two exceptions it lists are gone: the Check gets its delete, and the Reminder was already
  the rule rather than an exception to it.
