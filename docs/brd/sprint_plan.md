# The plan screen — approval packet

Status: **approved** 2026-08-26. Scenarios live in
[`tests/brd/planning.feature`](../../tests/brd/planning.feature), with the Sprint's own.
Batch: Phase 5.i
Prefix: `PL`, continuing the numbering of [planning.md](planning.md).
Sources: `INITIAL_PLAN` §Sprint accounting; `telegram/plan.py`,
`telegram/sprint.py::_render_planning`; the approved `PS-CAPACITY-004`, `SR-RUN-006`; the owner's
answers of 2026-08-26.

Four scenarios. The Sprint's own life is [planning.md](planning.md) and is approved separately.

## Why the batch exists

The plan is where the next Sprint is filled: the planned Actions as a table, the Backlog as the
keyboard, narrowed by saved Requests. It is the one screen where a saved Request is run by the owner
rather than by Safwa, and none of it is written down.

## What left this packet on the first reading

- **"Back goes where you came from, with the state it had."** The plan restoring its page and its
  picked filters is one case of a principle every screen follows, so it belongs to a Navigation
  packet of its own rather than to the Sprint. The behaviour ships unchanged in the meantime; the
  packet that owns it adds the prefix to `tests/brd/README.md`.
- **The retrospective.** Its own feature. What happens at the end of a Sprint is `PL-END-015` in
  [planning.md](planning.md), which also tears out the current `/retro` picture. The analysis, the
  advice and whatever the Sprint retro screen ends up showing are specified nowhere yet.

## Scenarios

### PL-PLAN-016 — The plan is the Sprint as a table and the Backlog as the keyboard

Status: approved
Sources: `INITIAL_PLAN` §Sprint accounting; `render_plan`, `handle_plan_start`

```gherkin
  Scenario: PL-PLAN-016 — The plan is the Sprint as a table and the Backlog as the keyboard
    Given one Action is planned and twelve are in the Backlog
    Then the planned one is a row with its effort, and the plan says what the whole plan costs
    And the Backlog is buttons, ten to a page (SPRINT_PLAN_PAGE_SIZE = 10), each with a way into the Sprint
    When the owner taps a planned Action's Return
    Then it goes back to the Backlog and the plan is redrawn in place, without a second screen
```

### PL-PLAN-017 — Filters narrow the Backlog, and every picked Request has to match

Status: approved
Sources: `INITIAL_PLAN` §Sprint accounting; `_resolve_filters`, `render_plan_filters`; `SR-RUN-006`

```gherkin
  Scenario: PL-PLAN-017 — Filters narrow the Backlog, and every picked Request has to match
    Given two saved Requests, and a Backlog Action only one of them returns
    When the owner picks both
    Then that Action is not offered, and only Actions both Requests return are
    When nothing matches
    Then the keyboard says how many of the Backlog matched instead of going blank
    And a Request that was deleted since it was picked is dropped, and the rest still filter
```

### PL-PLAN-018 — The plan's cost is shown against the capacity

Status: approved
Sources: the owner's answer of 2026-08-26; `_render_planning`, `render_plan`; `PS-CAPACITY-004`

```gherkin
  Scenario: PL-PLAN-018 — The plan's cost is shown against the capacity
    Given the capacity in Settings is 10 points and 13 points are planned
    Then wherever the plan's total effort is shown, the capacity is shown beside it, and the owner is told the plan is above it
    And the Sprint still starts
    When no capacity is set
    Then the total is shown and nothing is warned about
```

### PL-PLAN-019 — The plan says when the owner is tapping too fast

Status: approved
Sources: the owner's answer of 2026-08-26; `_is_a_burst`, Telegram's own rate limiting

```gherkin
  Scenario: PL-PLAN-019 — The plan says when the owner is tapping too fast
    Given every row in the plan is a Telegram link, which Telegram counts against the owner's account rather than against Safwa
    When the owner taps 8 of them inside 10 seconds (PLAN_LINK_BURST_TAPS = 8, PLAN_LINK_BURST_SECONDS = 10)
    Then they are told what is happening and that Telegram can stop opening bots for hours
    And the tap they just made still opens what it points at
```

## The reading taken on the capacity

The owner asked for the capacity "on the Planning screen, next to the planned effort total". Both
screens show that total — Planning as `Planned: 2 Actions · 5 EP` and the plan itself as
`In Sprint: 2 Actions · 5 EP` — and only the first carries the capacity today. `PL-PLAN-018` puts it
on both, since it is the same number answering the same question. Say so if only the Planning screen
was meant.

## Audit

| Scenario ID | Existing tests | Class | Decision | New tests |
|---|---|---|---|---|
| PL-PLAN-016 | `test_telegram_item_ui.py::test_the_plan_is_the_sprint_as_a_table_and_the_backlog_as_the_keyboard`, `::test_a_return_tap_moves_the_card_back_and_redraws_the_same_screen` | business_valid | rename and cite | +1 for the page size |
| PL-PLAN-017 | `test_telegram_item_ui.py::test_a_filter_that_matches_nothing_says_so_instead_of_an_empty_keyboard`, `::test_the_filter_screen_toggles_a_request_on_and_off` | business_valid | rename and cite | +1 for two Requests intersecting, +1 for a deleted one |
| PL-PLAN-018 | `test_telegram_item_ui.py::test_the_planning_screen_refuses_an_empty_plan` | business_valid, partial | extend and cite | +1 for the capacity beside the total on both screens |
| PL-PLAN-019 | none | missing | write | 1 |

`test_opening_a_card_from_the_plan_comes_back_to_the_same_page_and_filters` stays green and uncited
until the Navigation packet, the way `test_card_move_tool_rejects_a_terminal_stage` waited for the
stage ladder.

## The move, as it shipped

`telegram/plan.py` stayed where it is, with `telegram/sprint.py` and for the same reason — see
[planning.md](planning.md). `plan_cost` is the one function both screens call for the plan's total
and the capacity beside it, so the two cannot drift. The burst counter stays process-local state in
the adapter.

## What the batch must not do

- No new saved-Request behaviour: the filter runs the Request that already exists, under the same
  validation.
- No analysis, no advice and no picture — the retrospective is its own feature.
- No change to what the plan writes: a tap moves an Action between the Backlog and the Sprint stage
  through the same operation every other screen uses.

## Gates, as measured

Measured together with [planning.md](planning.md): `pytest -q` 678 passed / 3 skipped, `ruff check .`
clean, `prompt_prefix.json` byte-identical, metrics unchanged or lower.
