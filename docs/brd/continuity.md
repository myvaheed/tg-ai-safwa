# Continuity scenarios and Phase 4 audit

This packet covers visible dialogue Summaries and `data/memory.md`. Telegram history remains the
canonical dialogue source. `memory.md` remains an ordinary UTF-8 text file: every non-empty line is
one fact and blank lines carry no fact.

## Decision recorded

The owner corrected the Continuity contract on 2026-08-22:

- SQLite synchronization rows are an implementation detail, not a business outcome;
- an edited text file has no business-level "invalid format" state;
- importing a later local edit is a separate observable case from the initial read;
- Summary and Memory background generation use one foreground-priority and staleness policy.

## Scenarios

### CO-SUMMARY-001 — Automatic Summary waits for the dialogue threshold

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md` Summary section

Given canonical dialogue below 6,000 estimated tokens, an automatic Summary is not written. Given
dialogue at or above that threshold, the post-turn check may write one. `/summarize` may force a
Summary below the threshold.

Primary test: `test_summary_below_configured_trigger_requires_force`. The test imports
`SUMMARY_TRIGGER_TOKENS`; the business value is currently 6,000.

### CO-SUMMARY-002 — A new Summary includes the previous Summary

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md` Summary section

Given a previous Summary and newer canonical dialogue, the next Summary request includes both so
that the newly posted Summary can replace the old boundary without losing retained context.

Primary test: `test_new_summary_request_includes_previous_summary`.

### CO-SUMMARY-003 — An owner message arriving during Summary generation wins the race

Status: approved
Sources: current concurrency contract; owner clarification on 2026-08-22

Given Summary generation has read one canonical dialogue snapshot, when an owner message arrives
before that generated Summary is recorded, then the concurrent message wins: the result based on
the earlier snapshot is discarded. A later attempt can summarize the expanded dialogue.

Primary test: `test_owner_message_wins_race_with_in_flight_summary`.

### CO-MEMORY-004 — `memory.md` is the source of durable facts

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md` `memory.md` section; owner correction on 2026-08-22

Given `memory.md`, reading memory returns its trimmed non-empty lines in file order. Blank lines are
ignored. If the file is absent, memory contains no facts.

Primary integration test: `test_memory_store_reads_non_empty_lines_from_real_text_file`. It writes
an actual file under pytest's `tmp_path`; `MemoryFileStore.sync()` reaches `Path.read_bytes()` and a
real SQLite session. No file or repository mock participates.

### CO-MEMORY-005 — A later local edit is observed on the next synchronization

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md` local-edit rule

Given Safwa has already read `memory.md`, when the owner edits the file outside Safwa, then the next
file synchronization returns the edited facts rather than the earlier contents.

Primary test: `test_next_sync_observes_a_local_memory_edit`.

### CO-MEMORY-006 — An AI replacement cannot overwrite a newer local edit

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md` atomic-write rule

Given AI maintenance started from one file hash, when the owner edits `memory.md` before replacement,
then replacement is rejected and the owner's newer file remains byte-for-byte unchanged.
The run is not considered memorized: CO-SYNC-008 is the shared cursor rule that keeps the same
dialogue eligible for the next attempt.

Primary test: `test_ai_memory_write_rejects_a_stale_file_hash`.

### CO-SYNC-007 — Memory maintenance starts after its own cursor

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md` `/syncmem` rule

Given a successfully processed dialogue cursor, the next memory-maintenance read starts after that
cursor rather than rereading the general Summary window.

Primary test: `test_memory_maintenance_reads_after_its_own_cursor`.

### CO-SYNC-008 — The memory cursor advances only after a successful file write

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md` `/syncmem` rule

Given new dialogue, when the AI result cannot be applied or the file write loses a hash race, then
neither `memory.md` nor the processed cursor changes. A successful replacement advances the cursor
to the newest processed message.

Primary integration test: `test_stale_memory_write_keeps_local_file_and_cursor_unchanged`. A
scripted provider edits the real temporary file during generation; the real hash check rejects the
AI replacement and the real SQLite cursor remains unchanged.

### CO-SCHEDULE-009 — Configured Memory maintenance runs once per local day

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md` scheduled synchronization section

Given a configured local Memory sync time, once that time is due and no successful run was recorded
for the local day, one maintenance run may start. Another scheduler check that day does not run it
again.

Primary test: `test_due_memory_maintenance_runs_once_per_local_day`.

### CO-SCHEDULE-010 — `off` disables scheduled Memory maintenance

Status: approved
Sources: `archived_docs/MEMORY_HISTORY_USAGE.md` scheduled synchronization section

Given Memory sync time is `off`, scheduler checks do not start AI memory maintenance.

Primary test: `test_memory_maintenance_off_never_runs`.

### CO-GENERATION-011 — Foreground dialogue has priority over Summary and Memory

Status: approved
Sources: owner clarification on 2026-08-22

Summary generation and scheduled Memory maintenance acquire the same background-generation gate.
If an owner response is active, the background operation does not start. If dialogue revision
changes while it runs, the shared currentness predicate becomes false and its stale result is not
published.

Primary tests: `test_background_gate_does_not_start_work_while_foreground_is_active`,
`test_background_gate_invalidates_currentness_after_dialogue_revision_changes`, and
`test_due_memory_maintenance_waits_while_foreground_generation_is_active`. CO-SUMMARY-003 covers
the corresponding Summary race outcome.

## Existing-test audit

The traceability contract is [`tests/brd/continuity.feature`](../../tests/brd/continuity.feature).

| Scenario | Test decision |
|---|---|
| CO-SUMMARY-001 | Rename and retain the focused threshold/force test |
| CO-SUMMARY-002 | Rename and retain the request-content test; history-window tests remain adapter coverage |
| CO-SUMMARY-003 | Name the concurrent-arrival race explicitly and retain the focused test |
| CO-MEMORY-004 | Split initial read from the old combined initial-read/external-edit test |
| CO-MEMORY-005 | Add its own local-edit-after-first-sync test |
| CO-MEMORY-006 | Rename and retain the stale-hash test |
| CO-SYNC-007 | Rename and retain the independent-cursor test |
| CO-SYNC-008 | Exercise a real file-hash race and verify both file and cursor |
| CO-SCHEDULE-009 | Rename and retain the once-per-day test |
| CO-SCHEDULE-010 | Rename and retain the disabled-schedule test |
| CO-GENERATION-011 | Add focused tests for the single shared gate |

`test_restore_missing_memory_file_intentionally_clears_memory` remains a backup restoration test. It
is not evidence for a Continuity scenario. The old blank-line-invalid test and scenario are removed.

## Gate A approval

The owner's 2026-08-22 correction approves CO-SUMMARY-001 through CO-GENERATION-011 as written
above and explicitly rejects the former invalid-memory scenario.

## Gate B and Gate C result

Passed on 2026-08-22. Every approved scenario has its named focused test. Summary and Memory share
`GenerationGuard.run_background`; `memory.md` has no business-level format validation; the targeted
Continuity/Profile test set, the full project test set, and Ruff all pass.
