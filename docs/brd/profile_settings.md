# Profile and Settings scenarios and Phase 4 audit

This packet covers persisted owner profile values, their prompt precedence, the Settings screen, and
the Diary-specific internal trigger derived from Settings. It does not migrate Reminder or Sprint.

## Decision recorded

The owner corrected the Profile/Settings contract on 2026-08-22:

- About Me and Advisor instructions must appear after `memory.md` in provider context;
- numeric boundaries must be explicit in the executable `.feature` contract;
- each scenario needs its own focused, clearly named test;
- the Diary setting controls one Diary-specific internal trigger, not every Reminder whose text
  happens to mention Diary and not Sprint's own reminders.

## Scenarios

### PS-CONTEXT-001 — Explicit Profile context follows `memory.md`

Status: approved
Sources: owner correction on 2026-08-22

The provider context places `memory.md` first, then the current planning/profile block containing
About Me and Advisor instructions. This ordering makes the explicit current values the later
instruction when inferred memory disagrees.

Primary test: `test_explicit_profile_context_is_after_memory_in_the_prompt`.

### PS-FIELD-002 — Undeclared Profile fields are rejected

Status: approved
Sources: current domain contract

An update naming a field outside the seven declared Profile fields fails without changing persisted
Profile values or workspace revision.

Primary test: `test_profile_rejects_an_undeclared_field_without_changes`.

### PS-SPRINT-LENGTH-003 — Sprint length accepts 2 through 60 days

Status: approved
Sources: `SPRINT_LENGTH_MIN_DAYS=2`; `SPRINT_LENGTH_MAX_DAYS=60`

Sprint length accepts whole numbers from 2 through 60 inclusive. Values below 2 or above 60 are
rejected without changing the Profile.

Primary test: `test_sprint_length_accepts_2_to_60_days_only`.

### PS-CAPACITY-004 — Sprint capacity accepts a positive whole number or `off`

Status: approved
Sources: current Settings contract

Sprint capacity accepts an integer of at least 1 effort point. `off` stores no capacity. Zero,
negative values, and non-integers are rejected.

Primary test: `test_sprint_capacity_accepts_positive_points_or_off`.

### PS-CLOCK-005 — Memory and Diary clocks accept `HH:MM` or `off`

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md`; `archived_docs/DIARY_PLAN.md`

Memory sync time and Diary time accept a valid local 24-hour clock from `00:00` through `23:59`.
`off` stores no scheduled time. Other input is rejected.

Primary test: `test_scheduled_profile_clocks_accept_hhmm_or_off`.

### PS-DIARY-006 — Diary Reminder Settings synchronize the Diary System Reminder

Status: approved
Sources: owner clarification on 2026-08-22

Diary Reminder Settings consist of Diary time and Diary instruction. Changing either synchronizes
the Diary System Reminder identified by `system=True` and no Sprint. Diary time `off` removes that
System Reminder. An ordinary owner Reminder about Diary and Sprint-linked Reminders are not selected
or changed by this operation.

Primary test: `test_diary_reminder_settings_sync_only_the_diary_system_reminder`.

### PS-UI-SAVE-008 — Valid input updates the selected field and auto-closes its prompt

Status: approved
Sources: current Settings adapter

Given one selected Settings field, valid input updates that field, closes the text prompt, and
redraws Settings. Other Profile fields keep their values.

Primary test: `test_valid_settings_input_updates_selected_field_and_auto_closes_prompt`.

### PS-UI-INVALID-009 — Invalid Settings input keeps data and the prompt

Status: approved
Sources: current Settings adapter

Invalid input leaves Profile data unchanged and keeps the same field prompt open with its validation
message.

Primary test: `test_invalid_settings_input_keeps_data_and_the_same_prompt`.

### PS-TIMEZONE-010 — Settings displays timezone without an edit action

Status: approved
Sources: current bootstrap and Settings adapter

The current workspace timezone is visible on Settings. Timezone is not one of the editable Profile
fields and has no Settings edit action.

Primary test: `test_settings_shows_timezone_without_a_timezone_edit_action`.

### PS-REVISION-011 — One successful Profile update bumps revision once

Status: approved
Sources: workspace optimistic concurrency contract

One successful Profile update increments workspace revision by exactly one, including an update that
also synchronizes the Diary internal trigger.

Primary test: `test_profile_update_bumps_workspace_revision_once`.

## Existing-test audit

The traceability contract is
[`tests/brd/profile_settings.feature`](../../tests/brd/profile_settings.feature).

The old parameterized Telegram Settings test is replaced by focused tests for successful input,
rejected input, and display-only timezone. Button count is intentionally not a business rule: the
Settings screen may gain actions unrelated to editing Profile fields. The isolated "extra Diary
instruction reaches Reminder text" test is removed; that assertion belongs inside PS-DIARY-006
with identity, isolation, schedule, and deletion checks.

## Gate A approval

The owner's 2026-08-22 correction approves the scenarios listed above. The number and purpose of
Settings buttons is intentionally not contracted as business behaviour.

## Gate B and Gate C result

Passed on 2026-08-22. Every approved scenario has its named focused test. Profile context follows
`memory.md`; Profile and Settings are feature-owned; Diary reconciliation selects only its internal
trigger; the targeted Continuity/Profile test set, the full project test set, and Ruff all pass.
