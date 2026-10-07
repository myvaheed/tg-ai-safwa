# UI/UX improvements batch

Status of one owner request: twelve screen and context changes, what each was to do, what is in
the code now and what is still open. Scenario files in `tests/brd/` stay the contract; this page
only tracks the batch.

## Requested

1. After auto-clear (quiet Home) or /clear, delete every earlier bot message — screens, receipts,
   everything — except hook messages the owner has not read yet.
2. A navigation architecture in the shell, so Back always returns to the screen the owner came
   from (bug: a Value opened from the Home Dashboard returned to the Values list), reusable by
   other bots.
3. Home Dashboard order: date and time, Latest changes (5), Goals by priority (first 5), Values,
   Today (all items), Tracked time.
4. Remove Backlog and Today from the menu and delete /today. One Dashboard button: a rich table
   with columns Backlog, Sprint, Today, Done, at most 10 rows, newest change first, with a
   Goals/Actions toggle (a Goal shows by its own stage). Under it a Backlog button: a numbered
   rich-text list of links, 10 per page, left/right paging, the same toggle.
5. Item buttons in quick actions: no kind word (Goal, Subgoal, Action) and no stage word; the kind
   is the emoji; where one list mixes stages, the stage is an emoji too.
6. Reminder list buttons: the title first (the Card or Check title for a reminder made by Remind),
   then " · " and a short when/how often; no "from …" for a schedule reminder.
7. A clearer Proposal Review screen for a Reminder.
8. A Diary menu button: years (10 per page) and a "Last 7 days" button; a year opens its months,
   a month opens its days (10 per page), each screen headed by where it is; Last 7 days lists all
   seven days up to today with dates, and an empty day opens a "No entry for this day" screen.
9. A saved Request's screen: Back to the list in place of Menu.
10. (Left empty in the request.)
11. Advisor and Mutator context carries each Value's and each Tag's description, not only names.
12. Repeat marks: a finished instance reads `[✅3, 🔄#5]` (was `[🔄3, live #5] [🔄✓]`), the open one
    `[🔄]`; a Check answered Missed reads ❌ in place of ✅.

Decisions taken with the owner: the Bot API has no read receipts, so "unread" means a cue sent after
the owner's last message to the Advisor; the open-instance mark shows always, with the
done-today mark removed; a Check's mark follows its outcome; an empty day in Last 7 days still
opens.

## Done

### 1. Clear keeps only unread cues

`clear_draw_home` in [chat.py](../src/tg_agent_shell/telegram/chat.py) draws the new Home first,
then deletes everything above it, sparing kept conversation and every message of
`UNASKED_KINDS` (Cue, Passing Cue) newer than the owner's last `DIALOGUE_USER` message. The host's
clear takes the spared ids ([host.py](../src/telegram_llm/host.py)). Scenarios: TG-HOME-023,
HM-QUIET-003; [HOME_DASHBOARD.md](HOME_DASHBOARD.md) updated.

### 2. Shell navigation

- `Place` ([place.py](../src/tg_agent_shell/telegram/place.py)) is a screen address: action,
  arguments and the Place to go back to, carried in the callback token itself and cut at
  `NAV_DEPTH` levels. `Place.child` opens a screen below, `Place.but` redraws the same one.
- [navigation.py](../src/tg_agent_shell/telegram/navigation.py): `place_button`, `back_button`
  (↩️ Back, or ↩️ Menu when there is nowhere to go back), `place_link`, `go`, `open_home`.
- A link inside a screen's text (`place_link`, `?start=go-…`) opens its screen in place of that
  message: `SCREEN_LINK` in [callbacks.py](../src/tg_agent_shell/telegram/callbacks.py) claims the
  token and dispatches on the newest screen; the burst warning moved there (`LINK_BURST_TAPS`,
  `LINK_BURST_SECONDS`). This replaced the per-feature start-link patterns and the plan state
  kept in `UiSession`.
- `CallbackContext`, `TextInputScreen`, `choice_screen` and `paging_row` take Places. Cards,
  Checks, Values, Tags, Reminders, saved Requests, Sprint and Plan, Profile, Life, Retro and the
  [wallet example](../examples/wallet/wallets/telegram.py) are migrated.
- Scenarios SC-BACK-012 (reworded) and SC-LINK-013 (new); tests in
  [test_navigation.py](../tests/shell/test_navigation.py); table in
  [FEATURE_MODULES.md](FEATURE_MODULES.md).

### 3. Home Dashboard

[dashboard.py](../src/safwa/features/home/dashboard.py) draws the requested order:
`HOME_LOG_SHOWN` changes, `HOME_GOALS_SHOWN` Goals from `priority_goals`
([workspace_mutator/api.py](../src/safwa/features/workspace_mutator/api.py), shared with the
Mutator context), Values, every Today item, tracked time. Scenarios HM-ORDER-014, HM-LOG-009,
HM-GOALS-013.

### 4. Dashboard and Backlog

[board.py](../src/safwa/features/cards/telegram/board.py): `render_board` is the rich table
(`BOARD_ROWS` per column) and `render_backlog` the numbered link list (`BACKLOG_PAGE_SIZE` per
page, Previous/Next only where they lead), both with the Goals/Actions toggle. The menu button is
🗂 Dashboard (📊 is Retro's); Backlog, Today and /today are gone. Scenarios CD-BOARD-046,
CD-BACKLOG-047, PL-KEY-025; onboarding manual and README updated.

### 5. Item buttons

`item_button_label` in [presentation.py](../src/safwa/features/cards/telegram/presentation.py):
kind emoji, title, and the stage emoji from `STAGE_EMOJIS` only where stages mix, within
`ITEM_BUTTON_LIMIT`. Scenario CD-BUTTON-048.

### 6. Reminder list

`reminder_title` and `reminder_when` in
[screens.py](../src/safwa/features/reminders/telegram/screens.py): "title · when", the Card or Check
title for an item reminder, no start date for a schedule. Scenario RM-UI-023.

### 7. Reminder Proposal Review

[review.py](../src/safwa/features/reminders/telegram/review.py) returns a `ProposalScreen` with the
mode (Create, Edit, Delete), 🔁 When, ⏭ First and the words; an edit shows `field_diffs`.
Scenario RM-WRITE-008.

### 9. Saved Request screen

The item screen has Back to the list; result buttons use `item_button_label`.

### 12. Repeat marks — code done, not yet verified

[marks.py](../src/safwa/foundation/marks.py) builds `REPEAT_MARKER` from `REPEAT_DONE` or
`REPEAT_MISSED` and `REPEAT_LIVE`; the `ai_cards` and `ai_checks` views print the same words;
`card_title_marks` adds `REPEAT_OPEN_MARKER` to the open instance on screens only. Advisor, Heavy
Analyzer and Cards prompts, [DOMAIN.md](DOMAIN.md), scenarios CD-REPEAT-026, CD-REPEAT-032 and
CH-REPEAT-015 and their tests are updated.

## Remaining

- **Verify item 12.** Its tests have not run. The prompt snapshot still holds the old mark wording
  of the Advisor, Heavy Analyzer and Cards prompts, so the prompt-prefix test fails until it is
  updated:
  `rtk proxy uv run pytest tests/test_architecture.py::test_rule_i_prompt_prefix_is_byte_stable --snapshot-update`.
  Then run the affected tests (checks, cards, cards UI, checks E2E, AI SQL), the full suite, Ruff
  and the architecture scanner. Before item 12 the UI and wiring tests and the Reminders tests were
  green; the full suite was not run after the navigation batch.
- **Item 8, Diary browser.** Not started. Add "diary" to `MENU_LAYOUT` in
  [home/api.py](../src/safwa/features/home/api.py); Place-based screens in
  [diary/telegram.py](../src/safwa/features/diary/telegram.py) for years, months of a year, days of a
  month and the last seven days, each day opening `render_diary` with Back, and an empty day
  opening a screen with Back; paging through `paging_row`. New DI scenarios after DI-FIND-025,
  tests, onboarding manual and prompt snapshot.
- **Item 11, descriptions in context.** Not started. `workspace_context` in
  [state.py](../src/safwa/features/workspace_mutator/state.py) still prints "Active Values:" and
  "Available Tags:" as one line of names; list one per line with its description, then update the
  scenario wording, tests and prompt snapshot if the prefix moves.
