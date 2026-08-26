# Planning: the Sprint and the mode without one — approval packet

Status: **approved** 2026-08-26, third reading. Scenarios live in
[`tests/brd/planning.feature`](../../tests/brd/planning.feature).
Batch: Phase 5.h (the last business batch of Phase 5)
Prefix: `PL` — `features/planning`, which after this batch owns the Sprint and its accounting.
Sources: `INITIAL_PLAN` §Product rules and §Sprint accounting; `domain.start_sprint`,
`finish_sprint`, `expire_due_sprint`, `sprint_metrics`, `set_sprint_success_criteria`,
`sprint_length_days`, `_schedule_sprint_reminders`, `archive_settled_items`;
`features/cards/use_cases.sync_commitment_for_stage`; `features/planning/background.py`;
`telegram/sprint.py`; the approved `PS-SPRINT-LENGTH-003`, `PS-CAPACITY-004`, `RM-SYSTEM-022`,
`RM-FIRE-011`, `CD-ARCHIVE-022`, `CH-ARCHIVE-013`; the owner's answers of 2026-08-26.

Fifteen scenarios. The plan screen is the second packet, [sprint_plan.md](sprint_plan.md).

## What the words mean here

Two things are easy to mix up, and both are in this packet.

- **Planning** is the mode the workspace is in while no Sprint runs. There is no Today then.
- **A Sprint** is a fixed stretch of days with Success criteria that say what it must achieve.

Everything else the owner keeps — Cards, Checks, Values, Tags — is *the board*, and none of it
belongs to this packet. A Card that sits in the Sprint stage is a Card; what the Sprint records
about it is a commitment.

## Why the batch exists

Most of what is below already runs in production, spread across `domain.py`,
`features/cards/use_cases.py` and two Telegram modules. The batch writes the rules down, then moves
the Sprint into `features/planning` so `domain.py` stops owning it. `domain.py` is 710 lines today;
the Sprint leaving is what takes it under 600.

Two rules are **new behaviour** and are marked as such: `PL-CRITERIA-003` (an empty plan is refused
by the domain, not only by the screen) and `PL-END-015` (an ended Sprint is handed to Safwa as a
short summary instead of being announced by a receipt Safwa never reads).

## How the Sprint counts work, in one table

A Sprint keeps one row per Action it ever had in scope. Five numbers come out of those rows, and
all five are sums of effort points, never counts of Cards.

| Number | Which rows |
|---|---|
| committed | the Actions that were in Sprint or Today when the Sprint started |
| added | the Actions that came into Sprint or Today afterwards |
| removed | the Actions that were sent back to the Backlog and did not come back |
| completed | the Actions the owner finished as Done |
| cancelled | the Actions the owner finished as Cancelled |

"Remaining" is not one of them and is not stored. Whoever wants it subtracts.

## The decisions the owner made on the first reading

- **The Sprint is run by hand, through the screens.** Safwa has no tool for starting one, finishing
  one, or changing one. `PL-MODE-002` is that rule, and it replaces the earlier scenario about one
  Sprint at a time, which was a restatement of "it starts only from Planning".
- **A Sprint needs Success criteria and at least one Action.** Both doors refuse an empty plan, not
  only the screen.
- **A Sprint that closes itself is not a receipt.** The closing hands Safwa a short summary and
  Safwa tells the owner, the way a Reminder that comes due hands over its words. Safwa is given the
  summary so it does not go reading tables to build one.
- **The capacity is shown on the Planning screen**, next to the effort the plan already adds up to,
  and does nothing else for now. It comes from Settings.
- **Nothing else ends a Sprint.** No pause, no extension, no reopening. On the Sprint's last day the
  button stops saying "Finish early" and says `Finish Sprint`.
- **Back-to-where-you-came-from is not a Planning rule.** It is one principle for every screen and
  belongs to its own Navigation packet, so the scenario about the plan restoring its page and filter
  left this batch.
- **The retrospective is its own feature.** No analysis, no picture and no advice is specified here.
  What this batch owns is the short summary at the end of a Sprint and the `[Sprint retro](retro:N)`
  link inside it.
- **That link opens an empty screen.** The Sprint retro screen ships here with nothing on it, so the
  link is live from the first Sprint that ends and the retrospective feature has a screen to fill
  rather than a link to add.
- **The picture goes out now.** `/retro`, `analytics.py` and the `retrospective_png` message kind
  are torn out with this batch, the way the completion feedback loop was in Phase 5.b. The
  retrospective feature starts from a clean sheet.
- **A Sprint has no capacity of its own.** `capacity_effort_points` comes off `sprints` and nothing
  replaces it: the effort the plan added up to at the start is the `committed` figure, which the
  Sprint's own commitment rows already answer. The capacity stays one number in Settings, shown
  while planning.
- **The summary at the end of a Sprint is six lines**, listed below, all of them read off the
  Sprint's own record.

## Scenarios

### PL-MODE-001 — The workspace is either planning a Sprint or running one

Status: approved
Sources: `INITIAL_PLAN` §Product rules; `Workspace.mode`, `sync_bot_commands`, `render_today`

```gherkin
  Scenario: PL-MODE-001 — The workspace is either planning a Sprint or running one
    Given no Sprint is running
    Then the workspace is in Planning
    And Today has no screen, no menu button and no command
    When a Sprint starts
    Then the workspace is in Sprint and Today is available again
```

### PL-MODE-002 — The Sprint is the owner's to run, and Safwa only reads it

Status: approved
Sources: the owner's answer of 2026-08-26; `BOARD_TOOLS`, `start_sprint`, `finish_sprint`,
`SYSTEM_PROMPT`

```gherkin
  Scenario: PL-MODE-002 — The Sprint is the owner's to run, and Safwa only reads it
    Given the owner is talking to Safwa
    When they ask it to start the Sprint, finish it, or change its dates, its length or its Success criteria
    Then Safwa has no way to do any of it and says where the owner does it themselves
    And no proposal is ever written about a Sprint
    And starting and finishing happen on the Sprint screen, and the length and the capacity in Settings
```

### PL-CRITERIA-003 — A Sprint starts with words and with work

Status: approved — the empty-plan half is **new behaviour**
Sources: `INITIAL_PLAN` §Product rules and §Sprint accounting; the owner's answer of 2026-08-26;
`set_sprint_success_criteria`, `start_sprint`, `_render_planning`

```gherkin
  Scenario: PL-CRITERIA-003 — A Sprint starts with words and with work
    Given the next Sprint has no Success criteria and nothing planned
    Then Planning does not offer to start it
    When the owner sends Success criteria that are only spaces
    Then it is refused and nothing is saved
    When the owner has written Success criteria but no Action is in Sprint or Today
    Then starting is refused for that reason
    When at least one Action is in Sprint or Today as well
    Then Planning offers to start the Sprint
```

### PL-CRITERIA-004 — Success criteria outlive the Sprint they were written for

Status: approved
Sources: `INITIAL_PLAN` §Product rules; `Workspace.sprint_success_criteria`, `board_context`

```gherkin
  Scenario: PL-CRITERIA-004 — Success criteria outlive the Sprint they were written for
    Given a Sprint started with Success criteria
    When it ends
    Then those words are still there as the draft for the next Sprint, to edit or reuse
    And Safwa reads them as a draft and says no Sprint is running
```

### PL-START-005 — Starting a Sprint fixes its days and its number

Status: approved
Sources: `INITIAL_PLAN` §Product rules; `start_sprint`, `sprint_length_days`; `PS-SPRINT-LENGTH-003`

```gherkin
  Scenario: PL-START-005 — Starting a Sprint fixes its days and its number
    Given the Sprint length in Settings is 14 days (SPRINT_LENGTH_DAYS = 14)
    When the owner starts a Sprint
    Then it runs from the owner's today through the 14th day, that day included
    And its number is one higher than the highest number any Sprint has ever had
```

### PL-SCOPE-006 — Starting a Sprint takes what is already planned, at the effort it has then

Status: approved
Sources: `INITIAL_PLAN` §Sprint accounting; `start_sprint`, `SprintCommitment.effort_snapshot`

```gherkin
  Scenario: PL-SCOPE-006 — Starting a Sprint takes what is already planned, at the effort it has then
    Given an Action of 3 points in Sprint, an Action of 5 points in Today, and a Goal above them
    When the Sprint starts
    Then it committed to 8 points, and the Goal is not one of them
    When the owner later edits the 3-point Action to 8 points
    Then the Sprint still says it committed to 8 points
```

### PL-SCOPE-007 — Work that joins a running Sprint is counted apart

Status: approved
Sources: `INITIAL_PLAN` §Sprint accounting; `sync_commitment_for_stage`

```gherkin
  Scenario: PL-SCOPE-007 — Work that joins a running Sprint is counted apart
    Given a running Sprint
    When the owner moves a 2-point Backlog Action into Sprint or Today
    Then those 2 points are counted as added, and what the Sprint committed to does not change
    And an Action created straight into Sprint or Today is counted the same way
    And the next copy of a repeating Action is counted the same way
```

### PL-SCOPE-008 — Work sent back to the Backlog is counted as removed, and bringing it back undoes that

Status: approved
Sources: `INITIAL_PLAN` §Sprint accounting; `sync_commitment_for_stage`

```gherkin
  Scenario: PL-SCOPE-008 — Work sent back to the Backlog is counted as removed, and bringing it back undoes that
    Given a running Sprint that committed to a 5-point Action
    When the owner moves it to the Backlog
    Then those 5 points are counted as removed
    When the owner moves it back into Sprint or Today
    Then they are not counted as removed any more
```

### PL-SCOPE-009 — A Sprint records what each Action came to

Status: approved
Sources: `INITIAL_PLAN` §Sprint accounting; `finish_action`, `sync_commitment_for_stage`;
`CD-STAGE-017`

```gherkin
  Scenario: PL-SCOPE-009 — A Sprint records what each Action came to
    Given a running Sprint that committed to a 5-point Action
    When the owner finishes it as Done
    Then the Sprint counts those 5 points as completed
    When the owner reopens it
    Then the Sprint counts them as neither completed nor cancelled
    And finishing it as Cancelled instead is counted apart from Done
```

### PL-CONTEXT-010 — Safwa is handed the Sprint's number, its dates and its Success criteria

Status: approved
Sources: `INITIAL_PLAN` §AI advisor — "The system message deliberately carries no Sprint metrics";
the owner's answer of 2026-08-26; `board_context`

```gherkin
  Scenario: PL-CONTEXT-010 — Safwa is handed the Sprint's number, its dates and its Success criteria
    Given a running Sprint
    Then every turn hands Safwa its number, its planned dates and its Success criteria
    And none of the Sprint's counted effort is in what it is handed
    When no Sprint is running
    Then Safwa is told so instead, with the draft Success criteria for the next one
```

### PL-WARN-011 — A Sprint warns the owner before it ends

Status: approved
Sources: `INITIAL_PLAN` §Sprint accounting; `_schedule_sprint_reminders`; `RM-SYSTEM-022`

```gherkin
  Scenario: PL-WARN-011 — A Sprint warns the owner before it ends
    Given the owner starts a 14-day Sprint at 18:32
    Then Safwa warns them the day before the end date at 18:32, and again on the end date at 18:32
    And a 2-day Sprint is warned only on its end date (SPRINT_LENGTH_MIN_DAYS = 2)
    When the Sprint ends
    Then both warnings are gone
```

### PL-END-012 — The owner ends the Sprint, and on its last day the button says so

Status: approved — the button wording is **new behaviour**
Sources: `INITIAL_PLAN` §Sprint accounting; the owner's answer of 2026-08-26; `finish_sprint`,
`render_sprint`

```gherkin
  Scenario: PL-END-012 — The owner ends the Sprint, and on its last day the button says so
    Given a running Sprint with an unfinished Action in Today
    Then the Sprint screen offers to finish it early
    When the owner finishes the Sprint
    Then the workspace is in Planning
    And that Action is still in Today, already planned for the next Sprint
    And there is no way to pause a Sprint, to extend it, or to bring a finished one back
    When it is the Sprint's last day
    Then the same button reads "Finish Sprint"
```

### PL-END-013 — A Sprint nobody closed closes itself

Status: approved
Sources: `INITIAL_PLAN` §Sprint accounting; `expire_due_sprint`, `run_sprint_expiry`

```gherkin
  Scenario: PL-END-013 — A Sprint nobody closed closes itself
    Given a running Sprint whose end date has passed
    When the owner's own midnight passes, whatever the clock says elsewhere
    Then Safwa closes the Sprint
    And everything still open keeps its stage
    When it is one minute before that midnight
    Then the Sprint is still running
```

### PL-END-014 — A Sprint ending is when the workspace is tidied

Status: approved
Sources: `archive_settled_items`, `settled_cutoff`; `CD-ARCHIVE-022`, `CH-ARCHIVE-013`

```gherkin
  Scenario: PL-END-014 — A Sprint ending is when the workspace is tidied
    Given Cards and Checks that closed two Sprints ago (ARCHIVE_AFTER_SPRINTS = 2)
    When a Sprint ends, whether the owner closed it or it closed itself
    Then those Cards and Checks are archived
    And while the workspace is in Planning nothing is archived, however long it stays there
```

### PL-END-015 — A Sprint that ended is handed to Safwa, and Safwa is the one who says so

Status: approved — **new behaviour**
Sources: the owner's answer of 2026-08-26; `RM-FIRE-011`; `run_sprint_expiry`, `finish_sprint`,
`citation`

```gherkin
  Scenario: PL-END-015 — A Sprint that ended is handed to Safwa, and Safwa is the one who says so
    Given a Sprint that has just ended, by the owner's own hand or at midnight
    Then a short summary of it is written from the Sprint's own record, without Safwa reading anything
    And that summary is handed to Safwa the way a Reminder that comes due hands over its words
    And Safwa writes one message to the owner about how the Sprint went, ending with a link named "Sprint retro"
    And the owner's next question about the Sprint is answered from that message, not from a fresh trip to the tables
    When the owner taps that link
    Then a Sprint retro screen for that Sprint opens, empty until the retrospective feature fills it
```

## What the end-of-Sprint summary says

One turn's input for a small model, so it is short, and it is written by code — Safwa reads nothing
to produce it.

| Line | Example | Where it comes from |
|---|---|---|
| which Sprint, and when it ran | `Sprint 7, 12.08 – 25.08` | the Sprint row |
| how it ended | `you closed it` / `the end date passed` | the Sprint row |
| what it was for | `Success criteria: ship the release` | the Sprint row |
| the five effort figures | `committed 21, added 5, removed 3, done 18, cancelled 2` | the Sprint's commitments |
| how the Actions ended up | `14 finished, 2 cancelled, 4 still open` | the same rows, counted |
| what is still open | `Write the README, Call the bank, …` | the titles of those 4 |

The last line is what lets Safwa name the unfinished work and ask what to do with it, and it is also
the one that can get long: it is capped at the first few titles.

Anything past this table — patterns, categories, energy, comparison with earlier Sprints, advice —
is the retrospective feature's.

## For the retrospective packet, when it is written

The five effort figures are frozen when an Action enters the Sprint. Three of the inputs the old
`/retro` used were not: whether a Card is blocked, whether it has Hard Time, and which Values are
active were all read at the moment the picture was drawn. Unblocking a Card in September changed
what July's retrospective said. Deciding whether a retrospective is a record of what happened or
advice about what is true now is that feature's first question.

## Audit

| Scenario ID | Existing tests | Class | Decision | New tests |
|---|---|---|---|---|
| PL-MODE-001 | `test_telegram_item_ui.py::test_menu_offers_today_only_while_a_sprint_runs`, `::test_today_screen_is_closed_during_planning` | business_valid | rename and cite | — |
| PL-MODE-002 | `test_board.py::test_every_mutation_tool_reaches_the_board_or_the_diary` | business_valid, partial | extend and cite | +1 that no tool anywhere writes a Sprint |
| PL-CRITERIA-003 | `test_sprint.py::test_a_sprint_cannot_start_without_success_criteria`, `test_telegram_item_ui.py::test_starting_a_sprint_needs_criteria_and_a_plan`, `::test_the_planning_screen_refuses_an_empty_plan` | business_valid | rename and cite | +1 for the empty plan refused by the domain |
| PL-CRITERIA-004 | `test_sprint.py::test_board_context_asks_for_a_sprint_and_hides_today` | business_valid | extend and cite | +1 |
| PL-START-005 | `test_sprint.py::test_sprint_length_comes_from_settings_and_is_bounded`, `::test_default_sprint_length_is_the_constant` | business_valid, and the bounds half belongs to `PS-SPRINT-LENGTH-003` | split, rename and cite | +1 numbering test |
| PL-SCOPE-006 | `test_domain.py::test_sprint_snapshots_and_carryover` | business_valid, partial | rewrite and cite | +1 frozen-effort test |
| PL-SCOPE-007 | `test_domain.py::test_sprint_snapshots_and_carryover`, `e2e/test_advisor_flow_e2e.py::test_ai_card_proposal_to_repeat_sprint_and_retrospective` | business_valid | extend and cite | +1 for a Card created into scope |
| PL-SCOPE-008 | `test_domain.py::test_returning_to_sprint_scope_cancels_the_earlier_removal` | business_valid | rename and cite | — |
| PL-SCOPE-009 | `test_domain.py::test_reopening_a_finished_action_clears_its_sprint_result` | business_valid | rename and cite | +1 for Cancelled |
| PL-CONTEXT-010 | `test_sprint.py::test_board_context_names_the_sprint_and_today_actions` | business_valid | rename and cite | +1 that what Safwa is handed carries no figures |
| PL-WARN-011 | `test_sprint.py::test_starting_a_sprint_schedules_both_end_reminders`, `::test_a_two_day_sprint_only_warns_on_its_last_day` | business_valid | rename and cite | — |
| PL-END-012 | `test_domain.py::test_sprint_snapshots_and_carryover`, `test_telegram_item_ui.py::test_starting_a_sprint_needs_criteria_and_a_plan` | business_valid | rename and cite | +1 for the last-day button |
| PL-END-013 | `test_sprint.py::test_a_sprint_expires_only_after_local_midnight_past_its_end` | business_valid | rename and cite | — |
| PL-END-014 | `test_checks.py::test_nothing_is_archived_while_the_workspace_is_in_planning` | business_valid, negative half only | extend and cite | +1 for the positive half |
| PL-END-015 | none | missing | write | 3, one of them E2E through the provider |

`test_sprint.py::test_a_sprints_end_warnings_belong_to_safwa` already cites `RM-SYSTEM-022` and is
left alone. `test_board_context_lists_critical_cards_valued_first` is the board's state block, not
the Sprint, and moves with the packet that owns that block.

Deleted with the picture: the retrospective half of
`e2e/test_advisor_flow_e2e.py::test_ai_card_proposal_to_repeat_sprint_and_retrospective`, which
asserts a PNG came out. Its Sprint half stays and is cited by `PL-SCOPE-007`.

## The move, as it shipped

- `features/planning/model.py` owns `Sprint` and `SprintCommitment`;
  `features/planning/use_cases.py` owns starting, ending, expiring and counting one, plus the
  end-of-Sprint summary. `domain.py` fell from 710 to 547 lines.
- `features/planning/api.py` is what Cards calls, and it holds those four implementations rather
  than re-exporting them: `sync_commitment_for_stage`, `record_sprint_result`,
  `delete_commitments_of_cards` and `settled_cutoff` — the last one counts in Sprints, so it was
  never Cards' question. `features/cards/use_cases.py` no longer names `SprintCommitment`.
- `features/cards/api.py` is what Planning reads back: `CardStage`, `planned_actions` and
  `action_titles`, over the Card model alone. Cards and Planning need each other in both
  directions, and a door each way is a cycle; this is the split that has one direction only.
- `archive_settled_items` is composed in `domain.py`, and `domain.finish_sprint` and
  `domain.expire_due_sprint` are the two callers that run it. Planning's own `finish_sprint` ends
  the Sprint and hands it over; it does not archive.
- `features/profile/api.py` gained `sprint_length_days` and `capacity_effort_points`;
  `features/reminders/api.py` gained `create_sprint_reminder` and `delete_sprint_reminders`.
- **The Telegram adapters did not move.** `telegram/sprint.py` and `telegram/plan.py` are imported
  at module level by `callbacks.py` and `commands.py`, so a feature that imports `safwa.telegram`
  and is imported by it is the cycle `features/profile/screens.py` already dodges with a
  function-level import. They go in Phase 8 with the rest of the adapters.
- The hand-over in `PL-END-015` is a system Reminder that is already due, so the scheduler delivers
  it exactly as it delivers a fired one — the same gate, the same turn, the same retry.

## What the batch must not do

- No new Sprint column at all, no second active Sprint, no pause, no extension, no reopening a
  finished Sprint.
- Nothing on the Sprint retro screen. It is empty on purpose, and filling it is the retrospective
  feature's whole job.
- No mutation tool for the Sprint, and no proposal about one.
- No Sprint figures in the prompt or in what Safwa is handed each turn.
- No analysis of the finished Sprint beyond reading its own record — no categories, no energy, no
  advice, no picture.
- No change to what an Action's stage means, and no new writer of `effective_stage`.
- No sixth metric, and no stored "remaining".
- No Alembic.

## Gates, as measured

- `uv run pytest -q` 678 passed / 3 skipped; `uv run ruff check .` clean.
- `schema.json` moved on `sprints` alone, as declared: `capacity_effort_points` left it and nothing
  replaced it. `prompt_prefix.json` byte-identical — the hand-over is a per-turn request, not a
  prompt.
- `scripts/architecture_metrics.py`: 0 cycles, DoD #1 26, #2 0, #3 5 (`domain.py` left the list at
  547 lines), #13 0, Rule G 2, Rule H 26.
- `docs/brd/test_inventory.md` regenerated.
