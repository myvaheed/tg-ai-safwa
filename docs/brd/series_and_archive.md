# The archive and a repeat series — approval packet

Status: **approved** 2026-08-25, delivered in Phase 5.e.
Batch: Phase 5.e
Sources: `CLAUDE.md` §"Read-only SQL is triple-guarded", §Domain invariants; the approved
`CD-ARCHIVE-022` … `CD-ARCHIVE-024`, `CH-ARCHIVE-013`, `CH-REPEAT-009`, `SR-RUN-006`, `VL-READ-014`;
`ai_cards`, `ai_checks`, `card_children`, `card_checks`, `request_cards`, `domain.repeat_marker`,
`domain.live_repeat_instance_id`, `require_target`, `render_card`, `render_check`; the owner's
rulings of 2026-08-25 — archiving is a matter of sight, never a verdict, and an archived item that
shows is marked rather than left out.

Five new scenarios and one rewritten line in each of four approved ones. Nothing about the database
changes.

## Why the batch exists

Safwa answers a counting question wrong today, and it answers it wrong quietly.

The owner keeps a Card "Pull up 20 times" with a repeating Check "Pulled up today?". Every answer
opens the next instance, so the series grows, and two Sprints after each answer the archiver takes
that instance off the screens. `ai_checks` filters an archived Check out, so the question "how many
times did I do it, and how many did I skip" is answered from whatever has not been archived yet.

Measured against a database built for it:

| Question | Safwa answered | True |
|---|---|---|
| Pulled up: done / skipped | 1 / 1 | 2 / 1 |
| Times the repeating Action was closed | 2 | 3 |
| Weigh-ins across the series | 1 | 2 |

There is no wrong SQL in those runs. The rows were not there.

A second failure sits under the first: `ai_checks.card_id` may name a Card that `ai_cards` does not
carry, so a query that joins the two silently drops rows instead of failing.

And an archived item is treated as a thing that does not exist wherever one is met: a Goal's
children leave it out, a Request drops it, the citation loses its link, `open` says there is no such
Card, `render_card` refuses outright. An archived Card is out of the way, not gone.

## What archiving is for, after this batch

**A list by stage leaves an archived item out. Every other list shows it, marked.**

That is the whole of it, and it is what keeps a Done list from growing forever. The Cards under a
Goal, the Checks on a Card, the Cards a Check hangs on, the answer of a saved Request — each shows
what is there, and says which of it is archived.

## What changes

**The two views stop filtering `archived_at`.** Hiding an archived row is the workspace's job, and
no screen and no dashboard reads an `ai_*` view.

**Four lists stop filtering it too**, and are marked instead: the Cards under a Card
(`card_children`, and the copy of that query in the children screen), the Checks on a Card
(`card_checks`, and the copy of it in the Card screen), the Cards a Check hangs on, and the answer
of a saved Request (`request_cards`).

`card_checks` and `series_instances` become the same query once the filter goes. One survives.

**A series says it is a series.** Every row names the series it belongs to, and a row that was never
copied names itself. Today the first instance carries no series at all until the moment a second one
is made, so grouping by the series drops it into a nameless bucket together with every other
unrepeated row.

**A Check names the Card's series too.** The hardest question — every answer across every copy of a
repeating Action — becomes one flat query with no join.

**A Card stops listing its Checks in `ai_cards`.** That column filters archived ones out; once
`ai_checks` stops filtering, the two disagree about how many Checks a Card has. The column goes, and
`ai_checks WHERE card_id = N` is the one answer. `direct_values` and `direct_tags` stay: a Value and
a Tag are never archived and never repeat, so neither can disagree with anything.

**Two markers, one shape.** The repeat marker says ` [🔄2]` today, and both prompts tell the model
that number is an id. It is not: it is the instance's place in its series. It becomes
` [🔄2, live #7]` — the place, then the id of the open one — and stays ` [🔄2]` when the series
has ended. `#7` is already how this system writes an id, including in the hint the model gets when
it proposes a change to a closed repeat: "Retry this call with #7, the open one in its series."

The archive marker is new and works the same way: ` [📦]` on an archived Card and an archived
Check, in the views and on every screen that shows one. A Card that is both reads
`Run [🔄2, live #7] [📦]`.

**An archived Card and an archived Check open, and read as archived.** The citation keeps its link,
`open` stops calling an archived item missing, and `render_card`/`render_check` stop refusing one.

The archived screen is a branch inside each of those two renders rather than a `render_archived_*`
of its own. What differs is the controls — no field button, no answer button, no second trip to the
archive; Reopen for an Action that does not repeat, and Delete — while the body, the parent, the
`🔄 Current` button and the Values are the same reading. A second function would have been a copy of
that reading, and two copies of one screen drift apart: it is the shape this batch is removing from
`card_children` and `card_checks`, so it is not the shape to add here.

**A refusal says which refusal it is.** `require_target` answers both "there is no such item" and
"that item is archived" with one sentence, "does not exist or is archived", and sends the model
looking for the id again. That was harmless while the views filtered archived rows out. It is a lie
now, and the recovery is wrong: the id was right, and the owner is the one who can act on it.

## What does not change

- The database. No column, no index, no migration — the views are rebuilt at every startup.
- What is archived, when, and what may be archived by hand.
- The lists by stage: Today, the Sprint, and a list of one stage leave an archived Card out.
- An archived item is still not changed by a proposal, and only the owner takes a Card out of the
  archive, by reopening it (`CD-ARCHIVE-024`).
- Every filter that is a mechanism rather than sight, and each is left where it is: resolving a
  Value, Tag or Check by name; finding the open instance of a series; refusing an archived parent
  (`CD-TREE-006`); the two archivers themselves.
- The `🔄 Current` button on a Card screen and a Check screen. It already opens the open instance.

## Decisions the owner made

- **The Check column leaves `ai_cards`.** One place answers "which Checks are on this Card".
- **The marker's link is the button that already exists.** A citation stays one button with one
  target; nothing is added beside it.
- **The archived render ships here**, with the rule that needs it. A refusal that tells the owner to
  go and change the item by hand is worth nothing while the item cannot be opened.
- **A list shows an archived item and marks it.** Ruled for the Cards under a Card and for a
  Request; carried to the Checks on a Card and the Cards a Check hangs on, which are the same list
  written for the other entity.

## Scenarios

### CD-ARCHIVE-023 — An archived Card is marked, not left out

Status: approved (one line rewritten in an approved scenario)
Sources: the owner's rulings of 2026-08-25; `ai_cards`, `card_children`

```gherkin
  Scenario: CD-ARCHIVE-023 — An archived Card is marked, not left out
    Given a Goal with two finished Actions under it, one of them archived
    Then the archived one shows under its Goal marked "[📦]", and the lists by stage leave it out
    And the effort of both is still counted, and both still count as Cards that were completed
    And the Sprint it closed in still reports it
```

### CH-ARCHIVE-013 — An answered Check is archived two Sprints later

Status: approved (one line rewritten in an approved scenario)
Sources: the owner's rulings of 2026-08-25; `ai_checks`, `card_checks`

```gherkin
  Scenario: CH-ARCHIVE-013 — An answered Check is archived two Sprints later
    Given a Check that was answered
    When two Sprints have gone by since it was answered
    Then it is archived without anyone asking: still counted, and marked "[📦]" wherever it shows
    And its Card did not take it there, and it did not wait for its Card
    And the owner may archive an answered Check by hand before then
    And a Check that is still Pending cannot be archived
```

### SR-RUN-006 — A Request answers with the Cards it names, in its own order, each one once

Status: approved (one line rewritten in an approved scenario)
Sources: the owner's ruling of 2026-08-25; `request_cards`

```gherkin
  Scenario: SR-RUN-006 — A Request answers with the Cards it names, in its own order, each one once
    Given a Request whose query says what order it wants
    When it is run
    Then the Cards come back in that order
    And a Card the query named twice appears once
    And an archived Card comes back marked "[📦]"
    And a Card that no longer exists is skipped, rather than breaking the run
```

### VL-READ-014 — Safwa can start from a Value and find what is behind it

Status: approved (one line rewritten in an approved scenario)
Sources: the owner's ruling of 2026-08-25; `ai_cards`, `ai_checks`

```gherkin
  Scenario: VL-READ-014 — Safwa can start from a Value and find what is behind it
    Given a Value named "Health"
    When Safwa is asked how Health is going
    Then starting from the name Health it can find the Checks that carry it, and the Cards that
      carry it
    And the Checks are the evidence: they say how well Health is actually being held to
    And the Cards are the work: they say what is being done about it
    And an archived Card or Check is in both answers, marked "[📦]"
```

### CD-REPEAT-026 — A repeat series reads as one series

Status: approved
Sources: the owner's ruling of 2026-08-25; `domain.repeat_marker`, `domain.live_repeat_instance_id`,
`ai_cards`
Supersedes: `tests/test_domain.py::test_a_closed_repeat_names_its_place_in_the_series`
(characterization_valid — the marker's shape changes)

```gherkin
  Scenario: CD-REPEAT-026 — A repeat series reads as one series
    Given a repeating Action finished twice, so the series holds three Cards
    Then all three name the same series, and a Card that was never copied names itself
    And each finished one is titled "[🔄2, live #7]": its place in the series, then the open one
      (REPEAT_MARKER)
    And the open one is titled plainly, which is what says it is the one to work with
    And a Card that does not repeat is never titled with a marker
    And an archived one carries "[📦]" after its place in the series (ARCHIVE_MARKER)
    When the series has ended and no open one is left
    Then the last finished one is titled "[🔄3]" (REPEAT_MARKER_ENDED)
```

### CH-REPEAT-015 — A Check names its own series and the Card's

Status: approved
Sources: the owner's ruling of 2026-08-25; `ai_checks`, `checks/use_cases.py`

```gherkin
  Scenario: CH-REPEAT-015 — A Check names its own series and the Card's
    Given a repeating Check answered twice on a repeating Action that has been finished once
    Then every instance names the same Check series, and one never copied names itself
    And every instance also names the series of the Card it hangs on, and none if it hangs on none
    And counting every answer across every copy of that Action reads one list and joins nothing
    And the Checks on a Card are found by naming that Card, and in no other place
    And an answered instance is titled "[🔄2, live #7]" and the Pending one plainly (REPEAT_MARKER)
```

### CD-ARCHIVE-027 — An archived Card opens, and reads as archived

Status: approved
Sources: the owner's ruling of 2026-08-25; `render_card`, `move_card`, `CD-ARCHIVE-024`

```gherkin
  Scenario: CD-ARCHIVE-027 — An archived Card opens, and reads as archived
    Given an archived Card, cited in one of Safwa's answers or listed under its Goal
    When the owner taps it, or Safwa opens it
    Then it opens on a screen that says it is archived, offering nothing that would edit it
    And an Action that does not repeat offers Reopen, which takes it out of the archive
    And an Action that repeats offers no Reopen, by CD-ARCHIVE-024
    And a Goal and an Idea offer none: they leave the archive when a Card under them is reopened
    And deleting it is offered, and deletes it
```

### CH-ARCHIVE-016 — An archived Check opens, and keeps its answer

Status: approved
Sources: the owner's ruling of 2026-08-25; `render_check`

```gherkin
  Scenario: CH-ARCHIVE-016 — An archived Check opens, and keeps its answer
    Given an archived Check, cited in one of Safwa's answers or listed on its Card
    When the owner taps it, or Safwa opens it
    Then it opens on a screen that says it is archived, showing the answer it was archived with
    And no answer button is offered, and nothing else that would change it
    And a closed repeat still offers the button that opens the open one in its series
```

A Check has no delete button on any screen, archived or not, so this scenario claims none.
`CH-DELETE-014` says the owner may delete one; only the `remove` tool does today. That gap is
older than this batch and is left as it was found.

### PR-TARGET-001 — An archived item is not changed automatically

Status: approved
Sources: the owner's ruling of 2026-08-25; `require_target`

`PR` is a new prefix in use and `tests/brd/proposals.feature` is a new file: the rule is kept by
`features/proposals`, which every entity's proposal reaches.

```gherkin
  Scenario: PR-TARGET-001 — An archived item is not changed automatically
    Given an archived Card and an archived Check
    When Safwa proposes a change to either
    Then it is refused, and the refusal says an archived item is not changed automatically
    And Safwa ends its answer citing each one, for the owner to open and change by hand
    When Safwa proposes a change to an id that matches nothing
    Then it is refused for that reason instead, and Safwa is sent to find the id
```

## Audit

| Scenario ID | Existing tests | Class | Decision | New tests |
|---|---|---|---|---|
| CD-ARCHIVE-023 | `test_cards.py` ×1 | business_valid | extend | +1 view test |
| CH-ARCHIVE-013 | `test_checks.py` ×4 | business_valid | extend | +1 view test |
| SR-RUN-006 | `test_saved_requests.py` | contradictory on one line | rewrite that assertion | — |
| VL-READ-014 | `test_values.py` | contradictory on one line | rewrite that assertion | — |
| CD-REPEAT-026 | `test_domain.py::test_a_closed_repeat_names_its_place_in_the_series` | characterization_valid | rewrite and cite | +1 citation test |
| CH-REPEAT-015 | none | missing | write | 2 |
| CD-ARCHIVE-027 | none | missing | write | 2 |
| CH-ARCHIVE-016 | none | missing | write | 1 |
| PR-TARGET-001 | none | missing | write | 2 |

`test_telegram_item_ui.py` covers the lists that change and gains the marker assertions.

## What the batch must not do

- No new column on any table, and no `ai_*` view that filters a row out of Safwa's sight again.
- No analysis instruction added to `SYSTEM_PROMPT` or `BOARD_PROMPT`. The marker line is corrected,
  not extended; the routing heavy analysis needs is its own batch.
- No new subagent, and no `call_helper`.
- No list by stage that starts showing archived rows.
- No new way to change an archived item: Reopen and Delete are the two that already exist.

## Snapshots

`prompt_prefix.json` moves, for four reasons in each of `SYSTEM_PROMPT` and `BOARD_PROMPT`: both
describe the repeat marker as ` [🔄id]`, which is wrong today; neither knows the archive marker;
both list the columns of `ai_cards`, and the Check column leaves it; both list the columns of
`ai_checks`, which gains the Card's series. That is a declared change.

`schema.json` does not move. No model is touched.

## Gate expectations

`ruff check .` clean; `pytest -q` green, with no `xfail`; `test_brd_traceability.py` green with the
new prefix in use; `docs/brd/test_inventory.md` regenerated; metrics unchanged.
