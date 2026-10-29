# Test effectiveness review

Status: planned; 0 of 10 batches accepted. Preparing this plan does not count as reviewing
the assigned tests. Follow [Test criteria](TEST_CRITERIA.md) throughout.

## Objective and scope

Review whether the existing tests detect their claimed failures through justified setup,
meaningful assertions and readable scenarios. Strengthen weak protection, consolidate proven
overlap and preserve distinct boundary contracts. There is no deletion or mutation-score quota.

The planning inventory contains 180 test files, 1682 test functions and 3230 collected cases,
including 206 opt-in cases. Documentation checks contribute to the count. The file assignments
below cover every current test file once. Read shared fixtures, helpers, approved BRD and
application sources alongside them; using a test as evidence in another batch does not transfer
responsibility for reviewing its file. Assign any newly added file within these same ten batches.

The current checkout and branch are used. This plan authorizes no change to approved scenarios,
application prompts or schema. Follow the repository's existing rules if a finding requires one.

## Ten batches

The fault column identifies the representative failures to investigate in each area. Select
probes by behavior family and risk; a separate mutation for every test or parameter is unnecessary.

| Batch | Area | Files | Cases at planning | Representative faults | Status |
|---|---|---:|---:|---|---|
| 1 | Infrastructure and structural contracts | 14 | 938 | A broken registration or schema passes its checker; backup validation accepts a wrong key or incomplete pair | Planned |
| 2 | Runtime, turns, access and hooks | 19 | 282 | A stale lease publishes; cancellation leaves child work alive; locked access admits work | Planned |
| 3 | Proposals and Save/Discard | 15 | 174 | Save applies a stale change or commits partly; Discard writes; repeated approval applies twice | Planned |
| 4 | Cards, hierarchy and accounting | 18 | 257 | Completion loses a relationship or doubles totals; boundary ordering is reversed; reopening retains an incorrect stage | Planned |
| 5 | Checks, Values, Tags and reviews | 23 | 270 | A repeat loses its links; a review is bypassed; unlink or delete changes the wrong objects | Planned |
| 6 | Schedule calculations | 10 | 215 | Inclusive boundaries shift; local midnight or DST changes the day; a repeat consumes the wrong quota | Planned |
| 7 | Reminders and delivery | 15 | 275 | A retry duplicates delivery; the schedule advances before its firing is durably recorded; owner activity permits stale publication | Planned |
| 8 | Diary, memory, retro and helper | 13 | 147 | A stale write succeeds; unrelated memory observations are replaced; analysis alters a frozen result | Planned |
| 9 | Planning, Home and owner workflows | 28 | 353 | A repeat is counted twice; a switch is ignored; an outdated screen overwrites the current one | Planned |
| 10 | History, search, provider, voice and privacy | 25 | 319 | SQL escapes its scope; failed anonymization sends original text; a kept turn loses or duplicates content | Planned |
| Total | Whole suite | 180 | 3230 | Review all families, with deeper execution evidence for high-risk invariants | 0/10 accepted |

## Entry and baseline

1. At the start of batch 1, collect the current inventory and take a fresh full-suite coverage
   snapshot named quality-baseline. Record its revision, passing/skipped counts and source hashes.
   An earlier run with different documentation or tests is not this review's baseline.
2. Compare the inventory's file set with the assignments below. Resolve omissions, duplicates
   and additions before accepting a batch. Preserve the initial inventory for final ID comparison.
3. Review initial coverage gaps as well as executed code. Classify them as untested approved
   behavior, a boundary guard, opt-in execution or a code candidate requiring investigation.
   Missing execution is not proof that code is unnecessary.

Use the existing [coverage utility](../scripts/test_audit.py); do not introduce another framework
for the review. Commands run in PowerShell from the repository root:

```powershell
rtk proxy uv run python scripts/test_audit.py inventory
rtk proxy uv run python scripts/test_audit.py snapshot quality-baseline
```

The inventory is written to .test-audit/inventory.json and overwritten by the next inventory
command. Keep an initial copy before recollecting. Snapshots retain coverage contexts, JSON/HTML,
JUnit and source hashes. Snapshot names must be new; never overwrite accepted evidence.

## One cycle per batch

1. **Read and group.** Read the approved scenarios, current implementation, test bodies,
   fixtures and parameters for every assigned file. Group by failure and contract across levels.
   Do not begin by automatically adding a test for each uncovered function.
2. **Check the oracle and setup.** Identify the asserted result, how the relevant state arises
   and what evidence distinguishes the intended failure. Inspect tests that merely expect an
   exception, assert truthiness, repeat mock answers, alter private state or contain opaque
   payloads. These are review hints, not automatic reasons for deletion.
3. **Establish detection evidence.** For access, cancellation/currentness, transaction atomicity,
   SQL isolation and privacy, select representative plausible faults for the relevant behavior
   families. Use an existing regression's demonstrated failure or a temporary fault probe.
   For other families, probe when assertion strength or overlap remains uncertain. Include
   state transitions and ordering dependencies, not only incorrect scalar values.
4. **Apply one coherent change.** Keep justified tests. Strengthen an existing scenario where
   possible. Transfer unique assertions before merging or deleting overlap. Add a test only
   when an independent failure cannot be covered clearly by an existing scenario. Improve
   names and explain non-obvious setup without rewriting unrelated formatting.
5. **Verify the area.** Run affected domain, UI, shell and E2E tests, plus BRD traceability.
   Keep opt-in tests gated. Execute shell and application groups separately if their conftest
   imports conflict under partial collection; the complete root suite remains the final check.
6. **Run the common gate.** Restore all temporary faults, take one complete coverage snapshot,
   compare it with the previous accepted snapshot and quality-baseline, then run Ruff and the
   architecture scanner. One snapshot is already one full suite run. Re-run after a correction,
   not after every individual deletion.
7. **Record and commit.** Record reviewed families, changed or removed IDs, surviving assertions,
   probe evidence, coverage decisions and unexecuted opt-in paths. Use the commit description
   for details and update the batch's status here with its result and commit. Commit each
   accepted batch before continuing; keep all remaining work within these ten batches.

## Fault probes and acceptance

A probe must change the behavior the test claims to protect. Examples include omitting a write,
weakening a refusal, double-applying an operation or allowing an outdated result to publish.
Prefer an isolated process or narrowly scoped patch. Record the named fault, selected test,
observed assertion failure and passing result after restoration. Confirm application sources
are restored before the common gate and commit.

A syntax error, import failure, broken fixture, changed test expectation or unrelated crash
does not establish detection. If the selected tests pass with the fault, determine whether
the fault was reached and meaningful before concluding that protection is missing. Strengthen
the test for a real gap; explain an equivalent or unreachable probe. Do not weaken a contract
or alter application code merely to make tests green. An uncovered application defect needs
a reproducing test and an explicit fix under the approved behavior.

Accept a batch only when all assigned families have a decision, required risk probes have
explained outcomes, affected tests and the full local suite pass, and no unexplained coverage
loss remains. For each removal, name surviving tests and the preconditions and assertions
they preserve. Coverage equality alone does not satisfy acceptance.

Coverage comparisons require identical application sources, settings and dependencies. If
an approved fix changes them, validate its failing/passing regression separately, then measure
both test versions on the same corrected application revision. Preserve those paired reports;
do not compare shifted line numbers or silently reset away an unexplained loss.

For example, after batch 1 (later batches use the previous accepted label):

```powershell
rtk proxy uv run python scripts/test_audit.py snapshot quality-01
rtk proxy uv run python scripts/test_audit.py compare .test-audit/quality-baseline .test-audit/quality-01
rtk proxy uv run ruff check .
rtk proxy uv run python scripts/architecture_metrics.py
```

## Completion after batch 10

- Reconcile the original and final inventories, including parameter renames, added and removed
  cases, test-file assignments and opt-in IDs. Every changed family has a recorded decision.
- Reconcile fault probes by the high-risk invariant they exercise. State limits of the evidence;
  neither a sample of successful probes nor high coverage proves the absence of all bugs.
- Verify every removal's surviving behavior and every coverage loss's explanation. All required
  checks pass on restored application sources; no temporary faults or unsupported expectations
  remain. Prompt/schema and approved behavior changes, if any, follow their own contracts.
- Report the accepted snapshots and commits, strengthened tests, justified reductions, findings
  about product code and checks that were not run. A second full audit should not be necessary
  merely because unreviewed files or unresolved decisions were deferred.

## Assigned files

These lists are the assignment source. They include local tests and opt-in tests; shared E2E
files are reviewed in full by their assigned batch even when other batches use their evidence.

### Batch 1 — Infrastructure and structural contracts

- [tests/e2e/test_startup_e2e.py](../tests/e2e/test_startup_e2e.py)
- [tests/shell/test_registry.py](../tests/shell/test_registry.py)
- [tests/shell/test_standalone.py](../tests/shell/test_standalone.py)
- [tests/test_architecture.py](../tests/test_architecture.py)
- [tests/test_backup.py](../tests/test_backup.py)
- [tests/test_brd_traceability.py](../tests/test_brd_traceability.py)
- [tests/test_config.py](../tests/test_config.py)
- [tests/test_database.py](../tests/test_database.py)
- [tests/test_docs.py](../tests/test_docs.py)
- [tests/test_feature_modules.py](../tests/test_feature_modules.py)
- [tests/test_infrastructure.py](../tests/test_infrastructure.py)
- [tests/test_qa.py](../tests/test_qa.py)
- [tests/test_security.py](../tests/test_security.py)
- [tests/test_shell_wiring.py](../tests/test_shell_wiring.py)

### Batch 2 — Runtime, turns, access and hooks

- [tests/e2e/test_access_dispatcher_e2e.py](../tests/e2e/test_access_dispatcher_e2e.py)
- [tests/e2e/test_access_e2e.py](../tests/e2e/test_access_e2e.py)
- [tests/e2e/test_cue_turn_e2e.py](../tests/e2e/test_cue_turn_e2e.py)
- [tests/e2e/test_subagent_e2e.py](../tests/e2e/test_subagent_e2e.py)
- [tests/e2e/test_turn_steps_e2e.py](../tests/e2e/test_turn_steps_e2e.py)
- [tests/shell/test_access_shell.py](../tests/shell/test_access_shell.py)
- [tests/shell/test_mini_plan.py](../tests/shell/test_mini_plan.py)
- [tests/shell/test_wallet_subsession.py](../tests/shell/test_wallet_subsession.py)
- [tests/test_access.py](../tests/test_access.py)
- [tests/test_agent_runtime.py](../tests/test_agent_runtime.py)
- [tests/test_cues.py](../tests/test_cues.py)
- [tests/test_hooks.py](../tests/test_hooks.py)
- [tests/test_initiatives.py](../tests/test_initiatives.py)
- [tests/test_interruptions_ui.py](../tests/test_interruptions_ui.py)
- [tests/test_progress.py](../tests/test_progress.py)
- [tests/test_subagents.py](../tests/test_subagents.py)
- [tests/test_summary.py](../tests/test_summary.py)
- [tests/test_tool_watchers.py](../tests/test_tool_watchers.py)
- [tests/test_turn_ui.py](../tests/test_turn_ui.py)

### Batch 3 — Proposals and Save/Discard

- [tests/e2e/test_autoapproval_e2e.py](../tests/e2e/test_autoapproval_e2e.py)
- [tests/e2e/test_proposals_e2e.py](../tests/e2e/test_proposals_e2e.py)
- [tests/e2e/test_review_e2e.py](../tests/e2e/test_review_e2e.py)
- [tests/e2e/test_review_edit_e2e.py](../tests/e2e/test_review_edit_e2e.py)
- [tests/shell/test_preparation_conversation.py](../tests/shell/test_preparation_conversation.py)
- [tests/shell/test_proposal_actions.py](../tests/shell/test_proposal_actions.py)
- [tests/shell/test_proposal_fields.py](../tests/shell/test_proposal_fields.py)
- [tests/shell/test_proposal_switch.py](../tests/shell/test_proposal_switch.py)
- [tests/shell/test_wallet_flow.py](../tests/shell/test_wallet_flow.py)
- [tests/shell/test_wallet_restart.py](../tests/shell/test_wallet_restart.py)
- [tests/test_proposal_batch.py](../tests/test_proposal_batch.py)
- [tests/test_proposal_saves.py](../tests/test_proposal_saves.py)
- [tests/test_proposals.py](../tests/test_proposals.py)
- [tests/test_proposals_ui.py](../tests/test_proposals_ui.py)
- [tests/test_workspace_mutator.py](../tests/test_workspace_mutator.py)

### Batch 4 — Cards and hierarchy

- [tests/e2e/test_blocker_hook_e2e.py](../tests/e2e/test_blocker_hook_e2e.py)
- [tests/e2e/test_done_words_live.py](../tests/e2e/test_done_words_live.py)
- [tests/e2e/test_empty_parents_hook_e2e.py](../tests/e2e/test_empty_parents_hook_e2e.py)
- [tests/e2e/test_parent_completion_hook_e2e.py](../tests/e2e/test_parent_completion_hook_e2e.py)
- [tests/e2e/test_parent_hooks_e2e.py](../tests/e2e/test_parent_hooks_e2e.py)
- [tests/e2e/test_time_tracking_e2e.py](../tests/e2e/test_time_tracking_e2e.py)
- [tests/test_card_closing.py](../tests/test_card_closing.py)
- [tests/test_card_creation.py](../tests/test_card_creation.py)
- [tests/test_card_hierarchy.py](../tests/test_card_hierarchy.py)
- [tests/test_card_ordering.py](../tests/test_card_ordering.py)
- [tests/test_cards.py](../tests/test_cards.py)
- [tests/test_cards_ui.py](../tests/test_cards_ui.py)
- [tests/test_effort_tracking.py](../tests/test_effort_tracking.py)
- [tests/test_goal_markers.py](../tests/test_goal_markers.py)
- [tests/test_parent_completion.py](../tests/test_parent_completion.py)
- [tests/test_parent_hooks.py](../tests/test_parent_hooks.py)
- [tests/test_repeat_times.py](../tests/test_repeat_times.py)
- [tests/test_time_tracking.py](../tests/test_time_tracking.py)

### Batch 5 — Checks, Values, Tags and reviews

- [tests/e2e/test_action_review_hook_e2e.py](../tests/e2e/test_action_review_hook_e2e.py)
- [tests/e2e/test_also_track_live.py](../tests/e2e/test_also_track_live.py)
- [tests/e2e/test_checks_e2e.py](../tests/e2e/test_checks_e2e.py)
- [tests/e2e/test_goal_clarification_hook_e2e.py](../tests/e2e/test_goal_clarification_hook_e2e.py)
- [tests/e2e/test_value_clarification_hook_e2e.py](../tests/e2e/test_value_clarification_hook_e2e.py)
- [tests/e2e/test_value_link_hook_e2e.py](../tests/e2e/test_value_link_hook_e2e.py)
- [tests/e2e/test_value_unlink_hook_e2e.py](../tests/e2e/test_value_unlink_hook_e2e.py)
- [tests/test_action_review.py](../tests/test_action_review.py)
- [tests/test_check_review.py](../tests/test_check_review.py)
- [tests/test_checks.py](../tests/test_checks.py)
- [tests/test_checks_ui.py](../tests/test_checks_ui.py)
- [tests/test_goal_review.py](../tests/test_goal_review.py)
- [tests/test_habit_tracker.py](../tests/test_habit_tracker.py)
- [tests/test_habit_tracker_live.py](../tests/test_habit_tracker_live.py)
- [tests/test_method_reviews_live.py](../tests/test_method_reviews_live.py)
- [tests/test_tags.py](../tests/test_tags.py)
- [tests/test_tags_ui.py](../tests/test_tags_ui.py)
- [tests/test_value_link_review.py](../tests/test_value_link_review.py)
- [tests/test_value_ordering.py](../tests/test_value_ordering.py)
- [tests/test_value_review.py](../tests/test_value_review.py)
- [tests/test_value_unlink_cleanup.py](../tests/test_value_unlink_cleanup.py)
- [tests/test_values.py](../tests/test_values.py)
- [tests/test_values_ui.py](../tests/test_values_ui.py)

### Batch 6 — Schedule calculations

- [tests/e2e/test_schedules_e2e.py](../tests/e2e/test_schedules_e2e.py)
- [tests/e2e/test_series_goal_live.py](../tests/e2e/test_series_goal_live.py)
- [tests/test_months.py](../tests/test_months.py)
- [tests/test_schedule_bounds.py](../tests/test_schedule_bounds.py)
- [tests/test_schedule_load.py](../tests/test_schedule_load.py)
- [tests/test_schedule_periods.py](../tests/test_schedule_periods.py)
- [tests/test_schedule_today.py](../tests/test_schedule_today.py)
- [tests/test_schedule_total.py](../tests/test_schedule_total.py)
- [tests/test_schedules.py](../tests/test_schedules.py)
- [tests/test_timing_phrases_live.py](../tests/test_timing_phrases_live.py)

### Batch 7 — Reminders and scheduling delivery

- [tests/e2e/test_effort_tracking_reminder_e2e.py](../tests/e2e/test_effort_tracking_reminder_e2e.py)
- [tests/e2e/test_remind_phrases_live.py](../tests/e2e/test_remind_phrases_live.py)
- [tests/e2e/test_reminder_e2e.py](../tests/e2e/test_reminder_e2e.py)
- [tests/test_condition_phrases_live.py](../tests/test_condition_phrases_live.py)
- [tests/test_dont_disturb.py](../tests/test_dont_disturb.py)
- [tests/test_effort_tracking_reminder.py](../tests/test_effort_tracking_reminder.py)
- [tests/test_remind_lead.py](../tests/test_remind_lead.py)
- [tests/test_reminder_agent.py](../tests/test_reminder_agent.py)
- [tests/test_reminder_conditions.py](../tests/test_reminder_conditions.py)
- [tests/test_reminder_ends.py](../tests/test_reminder_ends.py)
- [tests/test_reminder_flow.py](../tests/test_reminder_flow.py)
- [tests/test_reminders.py](../tests/test_reminders.py)
- [tests/test_reminders_ui.py](../tests/test_reminders_ui.py)
- [tests/test_schedule_remind.py](../tests/test_schedule_remind.py)
- [tests/test_scheduler.py](../tests/test_scheduler.py)

### Batch 8 — Diary, memory and retrospective

- [tests/e2e/test_diary_e2e.py](../tests/e2e/test_diary_e2e.py)
- [tests/e2e/test_diary_photos_e2e.py](../tests/e2e/test_diary_photos_e2e.py)
- [tests/e2e/test_heavy_analyzer_e2e.py](../tests/e2e/test_heavy_analyzer_e2e.py)
- [tests/e2e/test_memory_e2e.py](../tests/e2e/test_memory_e2e.py)
- [tests/e2e/test_retro_e2e.py](../tests/e2e/test_retro_e2e.py)
- [tests/test_diary.py](../tests/test_diary.py)
- [tests/test_diary_photos.py](../tests/test_diary_photos.py)
- [tests/test_diary_ui.py](../tests/test_diary_ui.py)
- [tests/test_heavy_analyzer.py](../tests/test_heavy_analyzer.py)
- [tests/test_memory.py](../tests/test_memory.py)
- [tests/test_retro.py](../tests/test_retro.py)
- [tests/test_retro_analysis.py](../tests/test_retro_analysis.py)
- [tests/test_retro_charts.py](../tests/test_retro_charts.py)

### Batch 9 — Planning, navigation and owner workflows

- [tests/e2e/test_advisor_flow_e2e.py](../tests/e2e/test_advisor_flow_e2e.py)
- [tests/e2e/test_blueprint_e2e.py](../tests/e2e/test_blueprint_e2e.py)
- [tests/e2e/test_capture_e2e.py](../tests/e2e/test_capture_e2e.py)
- [tests/e2e/test_compass_e2e.py](../tests/e2e/test_compass_e2e.py)
- [tests/e2e/test_home_e2e.py](../tests/e2e/test_home_e2e.py)
- [tests/e2e/test_onboarding_e2e.py](../tests/e2e/test_onboarding_e2e.py)
- [tests/e2e/test_profile_e2e.py](../tests/e2e/test_profile_e2e.py)
- [tests/e2e/test_sprint_e2e.py](../tests/e2e/test_sprint_e2e.py)
- [tests/e2e/test_thought_paths_e2e.py](../tests/e2e/test_thought_paths_e2e.py)
- [tests/e2e/test_usage_e2e.py](../tests/e2e/test_usage_e2e.py)
- [tests/shell/test_navigation.py](../tests/shell/test_navigation.py)
- [tests/test_blueprint.py](../tests/test_blueprint.py)
- [tests/test_compass.py](../tests/test_compass.py)
- [tests/test_compass_method.py](../tests/test_compass_method.py)
- [tests/test_constellation.py](../tests/test_constellation.py)
- [tests/test_home.py](../tests/test_home.py)
- [tests/test_life.py](../tests/test_life.py)
- [tests/test_motivator_context.py](../tests/test_motivator_context.py)
- [tests/test_onboarding.py](../tests/test_onboarding.py)
- [tests/test_planning_ui.py](../tests/test_planning_ui.py)
- [tests/test_profile.py](../tests/test_profile.py)
- [tests/test_profile_ui.py](../tests/test_profile_ui.py)
- [tests/test_sprint.py](../tests/test_sprint.py)
- [tests/test_sprint_accounting.py](../tests/test_sprint_accounting.py)
- [tests/test_thoughts.py](../tests/test_thoughts.py)
- [tests/test_thoughts_ui.py](../tests/test_thoughts_ui.py)
- [tests/test_usage.py](../tests/test_usage.py)
- [tests/test_workspace_focus.py](../tests/test_workspace_focus.py)

### Batch 10 — History, search and provider boundaries

- [tests/e2e/live/test_telegram_live.py](../tests/e2e/live/test_telegram_live.py)
- [tests/e2e/test_history_e2e.py](../tests/e2e/test_history_e2e.py)
- [tests/e2e/test_saved_requests_e2e.py](../tests/e2e/test_saved_requests_e2e.py)
- [tests/e2e/test_screens_e2e.py](../tests/e2e/test_screens_e2e.py)
- [tests/e2e/test_search_e2e.py](../tests/e2e/test_search_e2e.py)
- [tests/shell/test_wallet_photos.py](../tests/shell/test_wallet_photos.py)
- [tests/test_ai_sql.py](../tests/test_ai_sql.py)
- [tests/test_asr.py](../tests/test_asr.py)
- [tests/test_chat_history.py](../tests/test_chat_history.py)
- [tests/test_chat_ui.py](../tests/test_chat_ui.py)
- [tests/test_citations_ui.py](../tests/test_citations_ui.py)
- [tests/test_diagnostics.py](../tests/test_diagnostics.py)
- [tests/test_horizon.py](../tests/test_horizon.py)
- [tests/test_log_events.py](../tests/test_log_events.py)
- [tests/test_provider.py](../tests/test_provider.py)
- [tests/test_provider_live.py](../tests/test_provider_live.py)
- [tests/test_pseudonymize.py](../tests/test_pseudonymize.py)
- [tests/test_requests_ui.py](../tests/test_requests_ui.py)
- [tests/test_saved_requests.py](../tests/test_saved_requests.py)
- [tests/test_screen_formatting.py](../tests/test_screen_formatting.py)
- [tests/test_search.py](../tests/test_search.py)
- [tests/test_similar_items.py](../tests/test_similar_items.py)
- [tests/test_telegram_llm.py](../tests/test_telegram_llm.py)
- [tests/test_tracing.py](../tests/test_tracing.py)
- [tests/test_voice_ui.py](../tests/test_voice_ui.py)
