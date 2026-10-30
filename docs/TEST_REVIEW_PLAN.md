# Test effectiveness review

Status: reopened. The previous ten-batch pass used selective deep review and did not establish
that every test was individually assessed. Its acceptance labels below describe that earlier
pass, not completion of the exhaustive audit. The new pass assigns each Luna reviewer with
high reasoning its own group of 20 collected cases; five reviewers divide 100 cases, subject
to the available concurrent slots. Every case needs an explicit verdict based on its full
test body, fixtures, parameters and neighboring tests. The first 20 cases received five
reviews before the owner clarified this allocation; later groups need one complete review.
The parent verifies questionable removal proposals against actual behavior. Follow
[Test criteria](TEST_CRITERIA.md) throughout.

The exhaustive pass starts at ccf901e9: 3237 collected cases, 1680 test functions,
180 files and 206 opt-in cases. Its baseline is quality-10-accepted. The frozen assignments
contain 166 groups of at most 20 cases, including 48 groups and 944 cases in batch 1.
Reports retain the original IDs even after a case is removed or consolidated.
The earlier records below remain historical. Exhaustive batches 1–4 are accepted as luna-01
through luna-04 in v9.286–v9.289; batches 5–10 are unfinished.

## Exhaustive batch 1

Accepted in v9.286, the commit carrying this record. All 944 original cases in 48 groups have explicit
Luna/high verdicts based on their full bodies, parameters, relevant fixtures and sources.
The parent checked exact ID sets and questionable proposals against actual behavior.

- Remove 547 executions with no asserted input: 15 empty public-name scans, 527 source
  docstring scans with no links or module paths, two Markdown link scans with no links and
  three document-name scans with no checked references. Keep all nonempty inputs in the
  existing vocabulary and documentation tests. Collection automatically includes a file
  when a relevant name or reference is added; all Python sources are still parsed.
- Combine the allowed model/API import examples in the existing Rule K checker test as
  allowed-data-and-api, retaining both imports and all forbidden-operation examples.
- Strengthen test_ag_search_059_the_text_model_is_loaded_into_the_one_index to observe
  startup's actual load of its own SearchIndex instead of manually initiating that load.
- Strengthen test_links_with_anchors_still_check_their_target with a real temporary Python
  source. Assert extraction from module, class, method and async-function docstrings, and
  distinguish an existing anchored target from a missing one.

Parent overrides: keep within-feature duplicate-agent refusal; a per-feature deduplication
fault fails that case while the cross-feature case passes. Keep Home's independent document
input despite a shared Menu token. Remove empty cases defended only by future references,
because dynamic discovery preserves that scope. A reported missing condition-phrase test
exists and resolves correctly; no repair. Bare filenames remain suffix checks, not proof
of ownership by one package.

Detection evidence: omitting automatic search loading passed the old test and fails its
replacement. A false ban on model imports remains detected after combining the allowed
examples. Returning no docstrings passed all 585 selected old checks; the strengthened
control now fails on the missing texts. Keeping fragments in target filenames passed the
old anchor example; the strengthened control rejects the extra false missing target.
Both repaired controls pass without faults. Temporary-document/source probes also confirmed
that adding references causes collection and that broken references are reported.

Affected documentation/architecture checks: 245 passed; startup checks: 4 passed. Temporary
discovery files were cleaned before the successful affected run. Snapshot luna-01: 2483 passed,
206 skipped in 135.73 seconds; Ruff and architecture checks passed. Comparing against
quality-10-accepted with identical source/settings/dependency hashes found no lost lines or
branches. No application source, prompt, schema or approved behavior changed.

Inventory reconciliation: 3237 to 2689 collected cases, with 1680 functions, 180 files and
all 206 opt-in IDs retained. The removed IDs are exactly the 547 empty cases and two old
allowed-import examples; their one combined replacement retains both imports. Every removed
ID belongs to the reviewed batch. Live/provider/model opt-ins stayed gated and were not executed.

## Exhaustive batch 2

Accepted in v9.287, the commit carrying this record. All 284 original cases in 15 groups have explicit
Luna/high verdicts with exact assignment IDs and no unreviewed cases.

Remove two equivalent pre-verification parameters from
test_commands_waiting_during_unlock_run_only_if_the_secret_was_already_verified: /tags and
/start card-1. Their messages wait at the same outer access gate before routing. Keep the
/start pre-verification refusal and all three post-verification command/deep-link routes,
including their expected screens and buttons. An artificial loss of the authentication
snapshot guard fails three old negative cases and still fails the surviving negative case;
all three positive routes pass in both faulty runs.

Parent checks rejected three proposed repairs. Replaying all locked attempts already fails
two neighboring ordering cases. Removing the hook index's deduplication makes the existing
two-subscription test fail with two evaluations instead of one; simultaneous daily/interval
Tick data is not emitted by the real producer. The Blueprint consumer already sends a real
sub call through ToolAdapters: bypassing its review fails
test_sub_blueprint_runs_unless_the_review_says_not_asked's refusing case. Keep these existing
boundaries and their current assertions instead of adding overlapping checks.

Affected access checks: 50 passed. Hook, watcher and Blueprint checks: 65 passed after the
fault processes ended. Snapshot luna-02: 2481 passed, 206 skipped in 228.12 seconds. Ruff and
architecture checks passed; application/settings/dependency hashes match both baselines.
Inventory: 2689 to 2687 cases, exactly the two removed parameters, with no new or renamed IDs.
All 1680 functions, 180 files and 206 opt-in IDs remain. No application or approved behavior changed.

Coverage investigation: no lines or branches were lost against quality-10-accepted. Against
luna-01 only registry.py's interval-deduplication arc 181→179 was absent. That arc was not in
the original baseline: luna-01 gained it incidentally when the unchanged startup search test's
background poll ran. Its recorded context is that startup test, not either removed access
case; the startup file is byte-identical to the accepted batch-1 version. Preserve both
comparisons and the context evidence; this explains the fluctuation without restoring an
unrelated parameter or adding a test solely for coverage.

## Exhaustive batch 3

Accepted in v9.288, the commit carrying this record. All 173 original cases in nine groups have explicit
Luna/high verdicts with exact assignment IDs and no unreviewed cases.

Keep the Save/Discard, dependency, refusal, rollback, callback and standalone-shell boundaries.
Correct the misleading name and PR-FAIL-014 citation of the existing Tag-discard callback
case: test_discarding_a_tag_proposal_skips_its_queued_link_without_failure now cites
PR-SAVE-010. Its assertions are unchanged: no Tag or CardTag is written, the queued link is
not reported as failed, and the request continues to its answer. Actual Save-failure checks,
including the standalone failed-save screen, retain their own PR-FAIL-014 coverage.

The renamed case and BRD traceability checks pass: 58 passed. Snapshot luna-03: 2481 passed,
206 skipped in 224.03 seconds; Ruff and architecture checks passed. Source/settings/dependency
hashes match. Inventory reconciliation confirms exactly one old ID replaced by its new name:
2687 cases, 1680 functions, 180 files and all 206 opt-in IDs remain. No test body assertion,
application source or approved behavior changed.

No lines or branches were lost against quality-10-accepted. Against luna-02, check_review.py
line 119 was absent: it returns before reviewing a Check that is already gone or answered.
This line was absent in the original baseline and gained in luna-01/luna-02 only under the
unchanged unclear-Checks test's asynchronous work. Both that source and test are byte-identical;
the Tag-discard rename removed no assertion that supplied it. Preserve the context and both
comparisons as an explained incidental gain fluctuation. The Check review batch will assess
that test's coordination; no check is added merely to restore the number.

## Exhaustive batch 4

Accepted in v9.289, the commit carrying this record. All 257 original cases in 13 groups have explicit
Luna/high verdicts with exact assignment IDs and no unreviewed cases. Keep the distinct Card,
hierarchy, completion, time-accounting and UI boundaries; no removal is justified here.

Strengthen test_cd_blocked_020_being_blocked_does_not_stop_anything to observe that the blocked
Action actually reaches Sprint before it is finished. A temporary early return from move_card
after its warning passed the old test, which later finished the still-unmoved Action. The
new assertion fails on Today instead of Sprint; the correct implementation passes all 52
Cards checks. No added test case, application change or approved behavior change.

Snapshot luna-04: 2481 passed, 206 skipped in 165.23 seconds; Ruff and architecture checks
passed. Source/settings/dependency hashes match. No line or branch loss against luna-03 or
quality-10-accepted. Inventory is identical: 2687 cases, 1680 functions, 180 files and all
206 opt-in IDs. No scenario attribution changed.

## Objective and scope

Review whether the existing tests detect their claimed failures through justified setup,
meaningful assertions and readable scenarios. Strengthen weak protection, consolidate proven
overlap and preserve distinct boundary contracts. There is no deletion or mutation-score quota.

The earlier planning inventory contained 180 test files, 1682 test functions and 3230 collected cases,
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
| 1 | Infrastructure and structural contracts | 14 | 938 | A broken registration or schema passes its checker; backup validation accepts a wrong key or incomplete pair | Accepted: quality-01, v9.276 |
| 2 | Runtime, turns, access and hooks | 19 | 282 | A stale lease publishes; cancellation leaves child work alive; locked access admits work | Accepted: quality-02, v9.277 |
| 3 | Proposals and Save/Discard | 15 | 174 | Save applies a stale change or commits partly; Discard writes; repeated approval applies twice | Accepted: quality-03, v9.278 |
| 4 | Cards, hierarchy and accounting | 18 | 257 | Completion loses a relationship or doubles totals; boundary ordering is reversed; reopening retains an incorrect stage | Accepted: quality-04, v9.279 |
| 5 | Checks, Values, Tags and reviews | 23 | 270 | A repeat loses its links; a review is bypassed; unlink or delete changes the wrong objects | Accepted: quality-05, v9.280 |
| 6 | Schedule calculations | 10 | 215 | Inclusive boundaries shift; local midnight or DST changes the day; a repeat consumes the wrong quota | Accepted: quality-06, v9.281 |
| 7 | Reminders and delivery | 15 | 275 | A retry duplicates delivery; the schedule advances before its firing is durably recorded; owner activity permits stale publication | Accepted: quality-07, v9.282 |
| 8 | Diary, memory, retro and helper | 13 | 147 | A stale write succeeds; unrelated memory observations are replaced; analysis alters a frozen result | Accepted: quality-08, v9.283 |
| 9 | Planning, Home and owner workflows | 28 | 353 | A repeat is counted twice; a switch is ignored; an outdated screen overwrites the current one | Accepted: quality-09, v9.284 |
| 10 | History, search, provider, voice and privacy | 25 | 319 | SQL escapes its scope; failed anonymization sends original text; a kept turn loses or duplicates content | Accepted: quality-10-accepted, v9.285 |
| Total | Whole suite | 180 | 3230 | Review all families, with deeper execution evidence for high-risk invariants | 10/10 accepted |

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

## Batch 1 review

Status: accepted in v9.276, the commit carrying this record. Entry revision: 770c8bc. Fresh quality-baseline:
3024 passed, 206 skipped, 146.42 seconds; 3230 cases. Its inventory is preserved as
.test-audit/quality-baseline-inventory.json. All 180 file assignments were checked for
omissions and duplicates before review.

### Family decisions

| Files | Decision and scope of the evidence |
|---|---|
| test_backup, test_database, test_security | Preserve actual encrypted files/WAL, wrong-key refusal, locking in another process, key authentication and interrupted pair replacement. Expand the existing restore-refusal scenario to missing key, changed database and an intact but mismatched pair. Each refusal checks its cause, unchanged file hashes and readable original data. Synchronize connection cancellation on the worker entering the real open operation instead of sleeping; assert cancellation and thread cleanup |
| shell/test_registry, test_feature_modules | Preserve real catalogue construction, fresh-process table discovery and handler/tool/presenter identities. Extend existing duplicate registration cases to views, callback actions and text-input flows: names in those separate catalogues could otherwise silently overwrite another feature. Keep the no-database-read sentinel during registration |
| test_architecture | Preserve positive and negative checker examples, feature map, provider schema contract, fresh database tables/index and prompt/schema snapshots. Dynamic prefix tests retain the volatile block and compare multiple turns; empty inputs would not establish prefix stability. These checks prove their stated structural properties, not semantic agreement of code with BRD |
| test_docs, test_brd_traceability | Preserve real document/source enumeration, scenario/file connections, prefix/identifier checks and checker counterexamples. Different document parameters concern different files. The checkers establish references and syntax, not the correctness of the referenced behavior |
| test_config, test_qa | Preserve provider defaults, explicit overrides, normalization, privacy selection and production/QA separation. QA resolution uses explicit fake credentials and never contacts Telegram or a provider. Distinct configuration doors are not interchangeable |
| test_infrastructure, e2e/test_startup_e2e | Preserve fresh-process import, crash recovery, actual startup storage/views, seed-once and privacy refusal before provider construction. Wait for the poll's second failed round with an event and always stop its task. Rename the view-read test to describe its positive result without claiming to prove all read isolation |
| test_shell_wiring, shell/test_standalone | Preserve button/flow/command discovery, Command filters, independent routers and the wallet subprocess with Safwa unimportable. Real screen navigation and catalogue discovery protect different boundaries; keep both. Fake remote interfaces do not establish live integration |

The test-function count remains 1682. Existing restore refusal gains three cases; existing
registration refusal gains three cases. The view-read test and restore-refusal function are
renamed to match their actual assertions. No assertion is discarded, and no application,
prompt, schema or approved scenario is changed.

ID reconciliation:

- tests/test_backup.py::test_a_wrong_passphrase_refuses_a_restore_before_any_live_file_is_touched
  is retained as test_a_refused_restore_changes_no_live_files[wrong-passphrase-passphrase].
  The same function gains missing-key, changed-database and mixed-pair parameters.
- tests/test_infrastructure.py::test_read_only_query_runner_reads_only_ai_views is renamed
  to test_read_only_query_runner_reads_published_views_without_notice with its assertions intact.
- shell/test_registry adds modules5-ai_entries, modules6-entry_note and modules7-entry_note
  to test_registration_refuses_duplicate_names. Existing five parameter IDs are unchanged.
- Raw inventory: 2 old IDs replaced by 8 IDs, net +6 cases. All 206 opt-in IDs and the
  assignment of every test file are unchanged.

### Detection probes

All faults below were applied only in independent test processes. Application files remained
unchanged. A normal affected-suite run is the passing control after the faulty processes end.

| Temporary fault | Observed detection |
|---|---|
| Ignore archive checksums | Before strengthening: all 15 backup cases passed. Afterward: changed-database fails because restore did not raise BackupError; the other 17 pass |
| Skip proving the archive's database opens with its key | Before: all 15 backup cases passed. Afterward: mixed-pair fails because restore did not refuse; the other 17 pass. Its updated key checksum ensures this exercises pair validation rather than the checksum guard |
| Allow duplicate views, callback actions or text-input flows | Three independent probes each passed all 6 original registry cases. After strengthening, each fails its own new collision case with the missing RuntimeError; the other 8 cases pass |
| Give Card proposals the Tag presenter | Existing registration-responsibility test fails the expected presenter identity assertion |
| Omit the fresh database's agent-step kind index | Existing fresh-schema test fails its index assertion |
| Allow duplicate module/agent/command/navigation names | All 5 existing duplicate-name cases fail because registration no longer refuses |
| Make the business-import checker report no violations | All 4 forbidden examples fail their assertions; the 2 allowed examples still pass |
| Make the documentation link checker accept a broken target | Existing anchor example fails its expected missing-link assertion |
| Skip finishing interrupted pair replacement on unlock | Existing interruption-at-step-4 test cannot unlock with the newly published passphrase, exposing the incomplete pair |
| Ignore the supplied database key and reuse a previously valid key | Both existing read-only and writable wrong-key cases fail because opening is no longer refused |
| Dispose without waiting for connection-opening workers | The synchronized cancellation test fails its final assertion with a still-live connection thread |

These are 13 distinct faults, including the three separate collision probes. Five faults
survived the old selected suites and are now detected. Compilation, import and fixture errors
were not counted as detection. The recovery probe fails on the expected readable-pair outcome;
the temporary interrupted write itself still occurs as configured.

### Initial gaps and limits

Initial backup gaps included archive shape, checksum and pair-opening refusals. Representative
reachable failures now exercise all three. Invalid ZIP/manifest versions, copy/integrity failures,
malformed key files and the POSIX lock implementation remain guards or platform paths, not
proven dead code. Other registry-construction refusals, unknown readers/tools and conflicting
proposal declarations remain supported boundary validation; these probes do not establish
exhaustive coverage of every invalid manifest.

Startup's successful downloaded PII model, image/transcriber/tracing integration, QA user
authorization and its live Telegram paths are not exercised by this batch. Local startup
failure and fake remote boundaries are retained without claiming those external paths ran.
Other initial gaps, including foreign-owner startup refusal, malformed configuration and
database argument/error fallbacks, are retained as untested guards rather than removed.

Validation: affected area 944 passed (934 application/structural checks and 10 shell checks).
Accepted snapshot quality-01: 3030 passed, 206 skipped, 205.00 seconds; 3236 cases in total.
Batch 1 grows from 938 to 944 cases. Comparison with quality-baseline reports no lost lines
or branches on identical application/settings/dependency hashes. Ruff passes; architecture
scan reports no violations and no cycles. The final inventory agrees with all 180 assignments.

## Batch 2 review

Status: accepted in v9.277, the commit carrying this record. Entry revision: bc860f6c.
All 19 assigned files were reviewed: 282 cases before the batch, 284 afterward.

### Family decisions

| Files | Decision and scope of the evidence |
|---|---|
| test_agent_runtime | Keep standalone execution, suspend/resume tokens and transcripts, interrupted adoption, child cleanup, exclusive ending tools, repair/tool budgets and child deadlines. The small custom host tests the reusable runtime without Safwa. Its memory-store assertions complement actual persisted sessions in E2E; they do not replace them |
| e2e/test_subagent_e2e, test_subagents | Keep routing ownership, fresh versus adopted sessions, receipt/forward rules, scoped SQL reads and conversation windows. Similar interrupted-Diary setup establishes different contracts: caller continuation, receipt, retained draft and interruption notice. Prompt/declaration checks protect published contracts; scripted providers do not prove real model choices |
| test_access, e2e/test_access_dispatcher_e2e | Keep exact salted-secret matching, gate order before handlers/downloads, edited-message privacy, lock/retry/startup and command races. Replace expectations derived from production length/distance constants with explicit five/six-character and three/four-edit examples. Extend the existing real-dispatcher replay scenario to preserve ordinary inputs just outside both guess boundaries. Synchronize unlock/reminder waiting at the actual idle wait and clean up the task even on assertion failure |
| e2e/test_access_e2e, shell/test_access_shell | Keep reminder delivery while locked, deferred hook preparation, locked proposal Discard and normal review after entry. The wallet callback test verifies another application's access integration; Safwa's settings/UI tests cannot replace it |
| test_summary, test_cues, e2e/test_cue_turn_e2e | Keep owner precedence, stale-history/currentness checks, background cancellation, release of claims/reviews and already-saved changes after failure. Queue tests distinguish registered-but-unsettled delivery, new rows, SQLite ID reuse, deferred hooks and expired reviews. Integration failures after Save and before screen drawing protect different cleanup points |
| test_hooks, test_initiatives, test_tool_watchers | Keep invalid wiring, switch ownership, matching boundaries, cancellation propagation, commit versus rollback, delayed wording and schedule semantics. RefuseTool failure must stop the call; ReviewTool failure lets it run and logs the cause. Identical-looking refusal cases exercise different effects and grants. Clock examples use explicit moments; source constants only generate configurable intervals, with approved limits asserted independently |
| test_interruptions_ui, test_turn_ui, test_progress, e2e/test_turn_steps_e2e | Keep foreground rejection, undeletable input, screen interruption/freezing, notice/progress lifecycle, step order and answer preservation after optional work fails. UI checks cover rendering and registration rather than only the runtime's return value. The typing/deadline tests retain real short waits where elapsed time is the tested behavior |
| shell/test_mini_plan, shell/test_wallet_subsession | Keep required-plan repair and the explicit no-plan control. Preserve the second application's multi-turn subsession, Save/Discard, cancelled continuation, expiry, exit, durable one-time result and history isolation. A subsession surviving lock/cancellation is distinct from an ordinary routed child ending with its request |

No test function or assertion is removed; no overlap was proven equivalent across all these
boundaries. The function count remains 1682. The existing replay scenario gains two parameters:
six-characters and four-edits. Its old [None] ID becomes [no-request] with the same absence-of-request
assertions; [text] and [recording] remain unchanged. No other existing IDs change. Total inventory:
3236 to 3238 cases; all 206 opt-in IDs and all 180 file assignments remain unchanged.
Application sources, prompts, schema and approved scenarios are unchanged.

### Detection probes

Eleven distinct faults were applied in isolated test processes. They changed no application
file. A passing affected-suite run on the restored application is the control. The two
constant probes also update direct imports in collected test modules, emulating a source
constant change before import; leaving the old imported value would hide the shared-oracle flaw.

| Temporary fault | Observed detection |
|---|---|
| Classify six-character text as a secret-word attempt | All 55 original access cases passed. After strengthening, the literal longer example and the six-characters dispatcher parameter fail; the latter exposes deletion of the legitimate attempted message. Other 55 cases pass |
| Accept four character edits as a secret-word attempt | All 55 original access cases passed. After strengthening, the literal XXXX sesame example and the four-edits dispatcher parameter fail for the same lost-message behavior. Other 55 cases pass |
| Let background work take an owner's foreground lease | Existing test_background_gate_does_not_start_work_while_foreground_is_active fails because the supposedly postponed callback returns a result |
| Release an owner's new lease when old background work finishes | Existing test_new_owner_turn_cancels_the_hook_without_losing_its_lease fails because source_message_id becomes None instead of 12 |
| Publish after-turn words without checking currentness | The same existing cancellation scenario fails its empty sent-messages assertion, even though the hook deliberately swallows cancellation |
| Stop closing unfinished children when a request ends | Existing runtime cancellation parameters start, resume and interrupted fail their expected ABANDONED child status; the completed-child control still passes |
| Finish a stored session without releasing its claim | Existing cue-turn failure-after-Save regression cannot resume the already-claimed child and fails its required outcome assertion; it never reaches its later final claim checks |
| Evaluate turn hooks while access is locked | All three existing locked-hook parameters fail on leaked publication/error text; their unlocked positive control remains part of the normal passing run |
| Swallow cancellation as a hook evaluation error | Existing test_check_failure_isolated_but_cancellation_propagates fails because CancelledError is no longer raised |
| Keep recorded changes after transaction rollback | Existing commit/rollback test finds a cue for rolled-back subject 1 beside committed subject 3 |
| Give the Advisor the workspace reader's SQL permissions | Existing AG-READ-027 E2E scenario fails: the Advisor's actual restricted query succeeds instead of being refused; the workspace reader still succeeds |

Nine faults were already detected; two survived the original selected suites and are now
detected at both function and dispatcher boundaries. All counted failures have zero fixture
or collection errors and concern the named result, refusal or state. No new framework or
per-test mutation requirement is introduced.

### Initial gaps and coverage decisions

The credentials module already had complete line/branch execution, yet the two changed
boundaries were undetected. The strengthened assertions address that gap without using
coverage percentage as an oracle.

Other initial gaps remain named limits, not dead-code evidence: invalid or competing claims,
missing/dangling stored sessions, resuming an interrupted caller after an exception, generic
multi-level caller delivery, unusual transcript layouts, direct TurnManager.begin conflicts
and cancellation of run_background's waiting caller. Access has unexecuted callback-at-unlock
and replay-handler-error fallbacks; the Cue queue has a default no-words preparer. These
guards are retained. The probes establish representative cleanup, access, transaction and
SQL isolation failures, not exhaustive scheduling or storage interleavings. No live Telegram,
provider or downloaded PII model execution is claimed.

Full snapshot quality-02 has no line or branch loss against quality-baseline. Against
quality-01, there are no lost branches and one lost line mark: checks/check_review.py:119,
outside this batch. Its former context is the unchanged
test_unclear_checks_are_asked_together_and_an_answered_one_is_left_out. The old data records
arcs 117 -> 119 -> 123, although line 119 returns from the function; the actual decision
arc 118 -> 119 is missing in both reports. Three isolated runs of that unchanged test pass
and leave line 119 unmarked. This is a line-trace anomaly, not evidence that this batch
removed protection. The early-return guard remains unproven; do not count the former mark
as behavioral coverage. Raw comparisons are preserved separately as
.test-audit/quality-02/losses-from-quality-01.json and losses-from-quality-baseline.json.

Validation: 341 affected/BRD cases pass (321 application/traceability and 20 shell).
Accepted quality-02: 3032 passed, 206 skipped, 137.38 seconds; 3238 collected cases.
Both comparisons use identical application/settings/dependency hashes. Ruff passes;
architecture scan reports no violations or cycles. The complete inventory reconciles
the one parameter rename and two additions, with no unexplained removal.

## Batch 3 review

Accepted in v9.278; entry revision 736c120e. All 15 assigned files were reviewed,
with the existing subagent E2E scenario used to preserve a removed declaration check.

| Family | Decision |
|---|---|
| test_proposal_batch, test_proposal_saves, test_proposals | Keep reducer transitions separate from actual transactions. Preserve stale-workspace refusal, ordered writes, commit-once, all-or-none rollback, hook facts, late decisions and retryable standalone Save. Replace mutation of an already-prepared effort value with an I/O failure after the second real domain write. Assert its specific error and that only the first two writes were attempted, alongside unchanged storage and no committed facts |
| e2e/test_proposals_e2e | Keep grouping, dependencies, partial preparation failures, Save/Discard, interrupted/expired queues, destructive confirmation, callbacks, receipts and continuation failures. Fix the expiry oracle to explicit 29/30 minutes rather than deriving both input and expectation from the implementation's limit |
| e2e/test_autoapproval_e2e, e2e/test_review_e2e | Keep allowlist, exact-request review, manual fallback, one head at a time, plan/response repair, held answers and discarded-request controls. Provider scripts establish adapter/runtime behavior; they do not establish model judgement |
| e2e/test_review_edit_e2e, test_proposals_ui | Keep uncommitted edits, rejected values, kept fields, stage rewrites, schedule re-reading, user-edit receipts, marked fields, escaping and feature-specific controls. Similar screens cover different owning handlers and fields; no equivalent removal was established |
| shell/test_preparation_conversation, shell/test_proposal_actions, shell/test_proposal_fields, shell/test_proposal_switch | Keep the second application's conversation handoff, consumed extra actions, kept fields/pages, switched entity Save/Discard and refused switch. Custom memo storage exercises the shell extension contract, not database transaction atomicity |
| shell/test_wallet_flow, shell/test_wallet_restart | Keep actual ledger writes/balance, Discard, reader scope, stale buttons after restart, and manual input/refusal. These are application-independent composition checks |
| test_workspace_mutator | Keep tool ownership, context content/order, scope, search selection and clocks. Remove the bare workspace_state flag assertion: the existing test_a_subagent_reads_the_tail_of_the_conversation_as_tagged_data checks the actual delivered workspace block before tagged conversation. Its first BRD citation now names WS-JUDGE-002; other conversation-window scenarios retain AG-ROUTE-002 |

The only removed ID is tests/test_workspace_mutator.py::
test_the_board_judges_a_change_against_the_state_it_is_given. Its surviving E2E test
fails when workspace state is disabled in the declaration and composed roster; it
also checks owner/Advisor roles and the conversation window. This verifies delivery,
not a real model weighing its proposal. No new test functions or parameters were added.
Batch cases fall from 174 to 173, functions from 1682 to 1681, total cases from 3238
to 3237. All opt-in IDs and the 180-file assignment remain unchanged.

Six isolated faults supplied detection evidence:

| Fault | Observed result |
|---|---|
| Commit each queued Save separately | Existing rollback test finds Card One left written; the commit-count control also fails. The strengthened test retains this detection with valid prepared changes |
| Ignore the workspace revision when writing kept Saves | Existing stale-queue test receives approved instead of failed results |
| Accept a second decision on an already-answered item | Existing transition table and late-press state-identity assertions fail |
| Replace the actual failing Save's error with Something went wrong | Old selected rollback test passed; strengthened test fails its disk I/O cause assertion |
| Change review expiry from 30 to 31 minutes, including imported aliases | All 63 selected proposal/UI cases previously passed. The explicit expiry scenario now fails because the review remains open at 30 minutes |
| Disable workspace state in the declaration and its composed roster | Both the removed flag check and surviving delivered-context E2E assertion fail before removal; the surviving E2E assertion still fails afterward |

All counted probes fail on the named assertion/state with zero collection/fixture errors.
Temporary faults exist only in isolated processes. Normal affected runs pass: 231 cases
before the one removal (213 application/BRD, 18 shell), then 81 targeted runtime/context,
rollback and BRD cases after removal. The final full suite includes every affected file.

Initial gaps retained as boundary guards include missing/already-closed proposals during
Edit/Switch/write, stale versions, late head/claim races, unknown presenters and error-label
fallbacks. Abstract presenter methods and unsupported field-value representations are not
evidence for dead-code deletion. Exact long-text and replacement-share marking boundaries
are not exhaustively fault-probed. Live provider judgement and Telegram were not run.

Accepted quality-03: 3031 passed, 206 skipped in 220.79 seconds. Both comparisons, against
quality-02 and quality-baseline, report zero lost lines or branches on identical application,
settings and dependency hashes. Raw reports are preserved by comparison label. Ruff and
architecture checks pass, with no violations or cycles. No application, schema, prompt or
approved scenario was changed.

## Batch 4 review

Accepted in v9.279; entry revision 24d42be. All 18 assigned files were reviewed.

| Family | Decision |
|---|---|
| test_card_creation, test_cards | Preserve kind/field validation, atomic creation with relationships, explicit completion, reopening, archive, deletion, repeat identity and audit facts. Strengthen unknown-Priority refusal: name the invalid enum value and assert the old Priority and version before a valid update can overwrite the evidence. Remove obsolete root_confirmed/expected_parent_version setup fields that the test helper silently discarded |
| test_card_hierarchy, test_goal_markers, test_card_ordering | Preserve multi-parent DAG paths, cycles/depth, distinct Action totals, closed ancestors, deletion preserving other parents, live SQL tree levels and rendered markers. Ordering examples independently establish date-window boundaries, nearest Value inheritance, focus, tie-breaks and SQL-owned LIMIT/OFFSET. Shared scalar outputs alone do not replace the separate SQL/Telegram boundaries |
| test_cards_ui, test_card_closing | Preserve real callback/input navigation, draft versus persisted changes, Check answers, closing-time/effort choices, Back, selectors, paging, citations, escaped titles and archived/repeating controls. Prepared editor rows name persisted UI state; remote calls alone are fake |
| test_effort_tracking, test_time_tracking, test_repeat_times | Preserve opt-out state retention, old buttons after a switch, independent time/effort axes, accounting on finished instances, no duplicate completion, unchanged open successors, exact multi-copy counts, ended series and frozen Sprint statistics. These paths have different state and transaction boundaries despite shared setup |
| e2e/test_blocker_hook_e2e, e2e/test_empty_parents_hook_e2e, e2e/test_parent_completion_hook_e2e, e2e/test_parent_hooks_e2e, e2e/test_time_tracking_e2e, test_parent_completion, test_parent_hooks | Preserve commit-to-hook-to-queue integration and late rechecks after user changes. A completed Action does not close its Goal; reopening it does not reopen a closed Goal. Shared-parent and orphan questions name only eligible current items |
| e2e/test_done_words_live | Keep gated realistic Russian/English completion phrases, reminder replies and repeated-count requests. Review only; no live-provider execution is claimed |

Five isolated faults establish detection:

| Fault | Observed result |
|---|---|
| Permit depth 9, updating imported limit aliases | Existing maximum-depth SQL/marker test fails: the SQL root reads 8 while the new nine-level domain tree reads 9 |
| Ignore a Goal's explicit Done during ancestor propagation | Existing closed-Goal scenario reads today instead of done |
| Omit ancestor propagation after field updates | Existing shared-Action effort scenario retains 5/8/8 instead of updated 8/11/11 |
| Use UTC instead of the owner's local day in ordering | Existing local-date scenario reverses the expected order. The valid probe preserves the fixture clock: 1 failure, 53 passing ordering cases |
| Assign invalid Priority before enum validation raises | All 8 original creation cases pass; the strengthened refusal fails on urgent replacing medium. The valid update remains a positive control |

The first four faults were already detected; the fifth exposes why an exception alone
was insufficient. All counted results have zero collection/fixture errors. No function
is added or removed. Two misleading IDs are renamed with their assertions intact:
test_ui_mutations_use_domain_services_and_are_audited becomes
test_card_changes_are_persisted_and_audited; and
test_committed_card_relationships_are_validated_propagated_and_audited becomes
test_card_relationship_changes_propagate_and_are_audited. The former uses domain calls,
not UI dispatch; the latter does not establish every refusal its old name implied.

Initial gaps retained include missing/archived targets, unsupported direct text fields,
invalid relationship kinds, empty tree queries and already-open/terminal operations.
They remain boundary guards, not deletion candidates. No new test is added merely to
execute them. Hook no-op fallbacks and quantitative limits outside the chosen probes
remain explicit limits of this review.

Validation: 305 affected/BRD cases pass; 9 live-provider cases remain skipped.
Accepted quality-04: 3031 passed, 206 skipped in 135.62 seconds. No lost lines or branches
against quality-03 or quality-baseline, using identical application/settings/dependency
hashes. Ruff and architecture pass with zero violations/cycles. Inventory remains
3237 cases, 1681 functions and 180 files; opt-in IDs are unchanged. No application,
schema, prompt or approved scenario changes.

## Batch 5 review

Accepted in v9.280; entry revision 1c40f51. All 23 assigned files reviewed; 270 cases retained.

| Family | Decision |
|---|---|
| Checks and Check UI/E2E | Keep independent observations, corrections retaining observation time, Card completion gates, Goal versus Action repeats, fresh pending copies, archive/delete, series counts, escaped links, paging and manual answers. Domain, SQL, callback and Save paths establish different boundaries |
| Values, ordering and Value UI | Keep independent links, transfer to the current Check, focus, deletion preserving Cards/Checks, case-insensitive storage uniqueness, dense priority orders, rollback and all live display surfaces. A shared implementation does not replace these consumer assertions |
| Tags and Tag UI | Keep Unicode names, storage constraint, ordinary Inbox behavior, delete/unlink and editor navigation. Strengthen the existing overlong-description refusal with unchanged description and version before rollback |
| Action, Goal, Check, Value and link/unlink reviews, including six hook E2E files | Keep commit-only triggering, changed/deleted/answered item races coordinated by Events, no review on repeat copies, switch/queue gates, current delivery and real Save after advice. Similar test shapes protect different owner data and events |
| Habit tracker and three live test modules | Keep conversion, retry, limits, Values and real Save wiring. Preserve gated language/judgement examples; live providers were not executed |

Five isolated faults were checked. Assigning a Tag or Value description before its validation
raised passed each original overlong-description scenario; each strengthened scenario now fails
on the changed description. Removing the late Check-review recheck fails all three existing
edit/answer/delete races. Asking on every missed count at least three fails at the fourth
answer. Omitting CheckValue transfer fails both independent-Check and repeating-Card scenarios.
Every counted failure is an assertion of the named behavior, with no collection/setup errors.
The probe preserves function identity so registered hook callbacks execute the substituted code;
an earlier attempt that replaced only a module binding was discarded. A separate Value probe
that failed on expired-object access was discarded as unrelated evidence.

The two added state assertions belong to existing refusal cases; no test IDs or counts change.
Other retained gaps include missing/archived targets, invalid tool modes, stale editor targets,
empty selector pages and review work whose target vanished before it began. These are boundary
guards, not dead-code evidence; this batch does not claim exhaustive race or provider coverage.

Validation: 271 affected/BRD passed, 56 opt-in skipped. Accepted quality-05: 3031 passed,
206 skipped in 134.98 seconds. No losses against quality-baseline. Against quality-04, the
previously investigated Check-review line 119 trace anomaly recurs: the unchanged grouping
test marked arcs 117→119→123 across a return, without the real 118→119 guard decision.
There are no lost branches or other lines; this is the same tracing discrepancy recorded
in batch 2, not evidence that the early-return guard is tested. Raw comparisons are retained
separately. Application/settings/dependency hashes match. Ruff and architecture pass. Inventory stays
3237 cases, 1681 functions, 180 files, with unchanged opt-in IDs. No application, prompt, schema
or approved scenario changes.

## Batch 6 review

Accepted in v9.281; entry revision 66059aa. All ten assigned files and their 215 cases reviewed.
Keep independent literal calendar examples for short/leap months, inclusive last days, quota
starts, local week/DST, current periods, interval early answers, finite totals and revision
history. Keep planning-load and midnight/restart tests: they exercise reserved executions,
frozen effort, SQL/UI counts, hook order and exclusion of unrelated work. They are not replaced
by the smaller date calculations. Scripted compiler/E2E paths establish preparation and Save;
the two live phrase modules remain gated and were not executed.

Four isolated faults: UTC instead of local date fails the existing DST/week assertion; moving
quota rollover from >= to > fails the fifth-completion boundary; moving the total guard from
>= to > opens an unwanted Check and fails the existing finite-total scenario. Replacing query
validation details with Something went wrong passes all five original malformed-query cases.
Those cases now assert invalid_arguments, the relevant date/range/type/missing-field cause and
the essential correction hint; all five fail on that substitution. Explicit parameter IDs keep
the original arguments0 through arguments4 IDs. No new tests or removals.

Retain initial gaps in uncompiled direct scheduling, already-expired windows, missing targets,
clipped-summary fallbacks and Remind boundary guards. They are not evidence for deleting code.
Monthly/DST examples cover specific transitions, not every timezone or calendar input.

Validation: 186 affected/BRD passed, 86 opt-in skipped. Accepted quality-06: 3031 passed,
206 skipped in 131.12 seconds. Zero lost lines/branches against quality-05 and quality-baseline
on matching application/settings/dependency hashes. Ruff and architecture pass. Inventory and
opt-in IDs stay unchanged: 3237 cases, 1681 functions, 180 files. No application, prompt, schema
or approved behavior changes.

## Batch 7 review

Accepted in v9.282; entry revision edc45b2. All 15 files, 275 cases reviewed and retained.
Keep schedule resolution, quiet-window/DST and lead boundaries, finite reminders, condition
counts, corrected/batched answers, fixed series membership, manual editing, proposal/Save,
startup reconciliation, polling recovery and effort questions on the finished copy. Unit date,
real-storage, Telegram and scripted-provider tests exercise distinct contracts. Two live phrase
modules remain gated. Background owner precedence and queue delivery also retain the shared
runtime evidence reviewed in batch 2; cancellation tests here are not substitutes for it.

The existing scheduler scenario claimed atomic persistence but checked only final success.
It now lets the real Cue insert happen, raises a named disk I/O failure, verifies no Cue or
schedule advance persisted, then retries successfully and verifies another tick adds nothing.
A temporary implementation that settles and commits before adding the Cue passed the old
scenario and fails the new unchanged-time assertion. The real operations and database stay in
the test; only the storage failure is injected.

A second existing scenario now compares the whole persisted Schedule with the requested one,
instead of checking only weekly kind and absence of the old interval. A Friday-for-Monday
substitution previously passed and now fails on weekdays. Remove the unused ScheduleKind
import. Two other faults were already detected: advancing from pickup time fails the exact
next-fire assertion; bypassing the waiting-words gate fails the no-second-batch scenario.
All four probe failures identify the named behavior, with no setup/collection failures.

Initial gaps retained include missing/deleted targets, malformed condition/config fallbacks,
unreadable tracker responses, exhausted first occurrences and stale UI controls. They are
boundary guards, not evidence of unused code. This review does not execute real Telegram,
providers or all possible interleavings.

Validation: 284 affected/BRD passed, 48 opt-in skipped. Accepted quality-07: 3031 passed,
206 skipped in 138.29 seconds. No losses against quality-baseline. Against quality-06 only
the known Check-review line 119 trace anomaly recurs, without any lost branches or other
lines; its prior raw arcs again cross the return (117→119→123), not the actual guard decision.
See batches 2 and 5. Both reports are preserved. Application/settings/dependency hashes match.
Ruff and architecture pass. Inventory stays
3237 cases, 1681 functions, 180 files; opt-in IDs are unchanged. No application, prompt, schema
or approved scenario changes.

## Batch 8 review

Accepted in v9.283; entry revision 9d81966. All 13 files and 147 cases reviewed; none removed.
Keep Diary local-day reads, single-day replacement, optional Feeling, proposal versus manual
editing, photo-only days, shared media rename/delete, album cleanup and navigation. Keep helper
SQL refusal/repair, scoped tools, output forwarding, budget and cancellation. Keep memory
observation identity, independent Sprint evidence, correction/idempotence, selection without
deletion and the real tick-to-Advisor path. Keep retro frozen accounting, live Diary input,
multi-stage analysis, owner cancellation, rerun/mark changes, aggregation and chart rendering.
Their shared fixtures do not make the storage, adapter and E2E boundaries interchangeable.

Three isolated faults: ignoring memory currentness fails the existing BUSY/unchanged-rows
scenario; deleting every Sprint's observations fails the existing correction scenario's
independent evidence count. Overwriting retro.done during record_analysis passed the existing
analysis-completion scenario. That scenario now captures the frozen retro before analysis and
compares it after the first analysis and after reruns; the same fault fails on the changed
record. The surrounding valid analysis and mark/rerun assertions stay intact. These are
behavioral failures, not collection/setup errors. The held AnalysisProvider now signals an
Event; cancellation waits on it directly instead of polling requests 500 times with sleeps.

Retain initial missing-target, malformed-read, stale-analysis and chart empty/fallback guards.
In particular, empty work after taking a lease is still a concurrency guard, not dead code.
Numeric/layout chart assertions validate rendered structure and sizes, not every visual detail;
scripted model claims do not establish real model judgement. No live services were executed.

Validation: 204 affected/BRD passed. Accepted quality-08: 3031 passed, 206 opt-in skipped in
133.31 seconds. Zero lost lines/branches against quality-07 and quality-baseline with identical
application/settings/dependency hashes. Ruff and architecture pass. Inventory stays 3237 cases,
1681 functions and 180 files; opt-in IDs are unchanged. No application, prompt, schema or
approved behavior changes.

## Batch 9 review

Accepted in v9.284; entry revision 5159c85. All 28 files and 353 cases retained. Review
the assigned assertions, parameters and helpers across levels, with deeper setup/source
inspection for refusal, accounting, currentness and switches. Family decisions:

| Families | Decision |
|---|---|
| Advisor flow, blueprint, compass/method, constellation, life, motivator context and workspace focus | Keep session resumption, independent reader context, selected Values, actual relationships and stale-version checks. Scripted answers establish the wired path, not model judgement |
| Home and shell navigation | Keep cache invalidation, owner activity during generation, current message identity, menu/back and quiet-time clearing. A successful dashboard render does not replace stale-publication checks |
| Sprint, accounting, planning UI and Sprint E2E | Keep frozen versus actual effort, removed/rejoined work, scheduled successors, reopen, manual/AI boundaries, dates, capacity, Key Actions and stale answers. SQL, saved records and screen assertions observe separate consumers |
| Profile, onboarding and their E2E/UI tests | Keep persisted switches/settings, refusal, prompt-context ordering, hook request cleanup, resumed onboarding and callbacks. Declaration checks complement execution rather than replace it |
| Capture, Thought paths, Thoughts/UI and usage/E2E | Keep default capture, plan/direct-create, pagination, source identity, conversion, local accounting intervals and exclusion of forwarded voice. Shared setup does not establish equivalent preconditions or outputs |

The existing PL-CRITERIA-003 scenario now identifies empty-criteria refusals and checks
unchanged criteria/revision, unchanged workspace mode/active Sprint/revision and no Sprint
row after the refused starts. Its valid start remains the positive control. Assigning the
blank criteria before validation passed the old test and now fails its unchanged-state
assertion. No new case or helper is needed.

Four other process-local faults are already detected by retained assertions: publishing
Home words after owner activity edits the chat unexpectedly; accepting the old title's Key
Action answer overwrites the newer mark; omitting hook request cleanup retains the disabled
hook's Cue; retaining the predecessor's reserved repeat count produces committed 25 instead
of 15 and removed 10 instead of 0. Each failure is behavioral, without fixture/collection errors.

Retain initial missing/deleted-target, stale-screen, no-work, unreadable classification and
onboarding recovery guards. Missing execution does not justify deleting them. The probes
sample concurrency and accounting risks; they do not enumerate every interleaving.

Validation: the corrected planning/domain/UI/E2E group passes 69 cases; the other 341
affected/BRD/shell controls pass. Accepted quality-09: 3031 passed, 206 opt-in skipped in
206.15 seconds. Zero lost lines/branches against quality-08 and quality-baseline on identical
application/settings/dependency hashes. Ruff and architecture pass. Inventory remains 3237
cases, 1681 functions and 180 files; all opt-in IDs stay unchanged. No application, prompt,
schema or approved scenario changes.

## Batch 10 review

Accepted in v9.285; entry revision b5ed570. All 25 assigned files
reviewed by assertions, parameters, helpers and failure family, with deeper setup/source
inspection for SQL, history, privacy, refusal and resource boundaries. Family decisions:

| Families | Decision |
|---|---|
| Chat history/UI and history E2E | Keep whole turns, cleared reads, receipts, role/time/quote layout, edits, clear boundaries, subsession exclusion, long answers and delivery on the last part. Kept storage and the next provider request are distinct observations |
| AI SQL, saved Requests, Request UI and Request E2E | Keep scanner versus actual encrypted reads, reader scopes, nested/recursive CTEs, typed references, missing items, SQL ordering versus display caps, refusal, stale Save and navigation. Different SQL parameters exercise different lexical/scope rules |
| Search, similar items and search E2E | Keep word/meaning ranking, threshold boundaries, filters, index refresh, model changes, failure fallback, open-item selection and actual reader wiring. Controlled vectors establish ranking, not real embedding quality |
| Provider, pseudonymization, Horizon and live provider | Keep wire/config overrides, raw invalid arguments, signatures, portable IDs, retries versus deliberate silence, returned identifiers, stable placeholders and fail-closed behavior. Keep live model/GPU checks gated; fake finder tests do not establish recognition accuracy |
| ASR and voice UI | Keep API/CPU/CUDA boundaries, decode-time fallback, transcript joining, progress, refusal before download and exactly one owner turn. Transport fakes and script outputs are explicit boundaries |
| Citations, formatting and screen E2E | Keep safe HTML, code versus prose, missing targets, compact saved labels, navigation, stale/single-use callbacks, rejected screen cleanup and real persisted claims. Similar rendering checks concern different surfaces |
| Wallet photos and standalone chat package | Keep another application's photo labels/albums, stored media, rename Save/Discard, stale media, image-off behavior, tool grants and the example's next-turn history. Static package vocabulary/import checks protect the declared package boundary |
| Diagnostics, log events and tracing | Keep real revision/mode, manual versus AI audit records, deleted-ID reuse, Sprint attribution, provider/tool trace ancestry, cancellation/error propagation and disabled-tracing controls |

Strengthen the existing Request-name refusal scenario with unchanged name/version after
duplicate and blank updates, the exact two stored names after refused creation, and an accepted
rename. Assigning the refused name before validating passed the old test and now fails on
the changed name.

The argument-masking scenario becomes test_call_arguments_are_masked_in_valid_and_malformed_json
with nested-json and malformed-json parameters. The nested case retains the original string
and numeric assertions, adding a name inside an array of objects. The malformed case exercises
a reachable gateway contract: invalid model argument JSON is retained raw for tool repair.
Two faults, bypassing arrays and returning malformed arguments unchanged, each passed all
eight original privacy cases. Each now fails its own outgoing-content assertion; the other
eight pass. No real identifiers or external requests are used.

Remove test_provider.py::test_scripted_provider_needs_no_monkeypatching: its result was the
preconfigured fake answer's identity and its input was the fake's recorded request. Surviving
test_pseudonymize.py::test_ag_privacy_063_the_provider_reads_placeholders_numbered_by_kind and
test_placeholders_keep_their_numbers_as_the_transcript_grows assert actual transformed requests
across messages/steps; test_tracing.py::test_disabled_tracing_keeps_the_existing_turn_and_produces_no_spans
asserts the completed runtime answer and executed read. All use the same ScriptedProvider;
coverage contexts confirm these consumers exercise its return path. The unused test import is removed.
The only unique execution was its empty aclose method, discussed below; there was no close
assertion or resource in that fake to preserve.

Four other faults are detected by retained tests: widening a reader's SQL scope and allowing
an inner CTE to authorize an outer base table each lose the required refusal; sending the
original request after finder failure loses the required error; keeping old read results
fails the cleared-result comparison in history and E2E. The storage-history scenario proves
actual rows are cleared, while the E2E scenario proves the next request uses that representation.

Completion reconciliation also revisits one batch-1 boundary: backup.py:68–69 and branch
67→68 disappear from the intermediate quality-10-final snapshot. Their old contexts are
unchanged backup/security tests which happen to make two archives in one wall-clock second.
The existing committed-WAL scenario now takes two backups at the same explicit NOW, changes
the committed data between them, asserts distinct paths and unchanged earlier archive bytes,
and restores both independent contents. Disabling collision handling passed the old test and
now fails on the reused path. This is a demonstrated oracle gap, not dead code or a reason to
accept lost backup protection. The file remains assigned to batch 1; this completion finding
does not create another batch or duplicate its assignment.

All eight fault families fail for the stated behavior, without collection/setup errors.
Keep initial SQL cap/authorizer and malformed-input guards, search load/refresh fallbacks,
missing targets, transport/decode failures and delegated close paths. None is proven unused.
Horizon model loading/inference remains opt-in. These probes do not establish all timing
interleavings, real model judgement, recognition accuracy or live Telegram compatibility.

Validation: 369 affected/BRD/shell controls pass with 7 opt-in skips before the final privacy
extension; the 40 affected privacy/provider/tracing controls pass afterward. The 24 backup
and security controls pass after the completion finding. Accepted quality-10-accepted:
3031 passed, 206 opt-in skipped in 135.56 seconds. Ruff and architecture pass with no violations
or cycles. Source/settings/dependency hashes match every accepted snapshot and the baseline.

Against quality-09 and quality-baseline, the only lost execution is
llm_gateway/testing.py:25, ScriptedProvider.aclose's return None. Its sole prior context is
the removed fake echo test, with no assertion about close. This fake owns no client or other
resource; the empty method fulfills LlmProvider's interface. Retain the method and accept
that explained loss rather than add a test merely to execute it. There are no lost branches
and no lost lines of product behavior. Both labeled comparison reports are preserved.

## Completion reconciliation

All ten batches are complete, each committed separately as v9.276 through v9.285. Their
accepted snapshots are quality-01 through quality-09 and quality-10-accepted. Intermediate
quality-10 and quality-10-final are retained as diagnostics, not the final accepted state.
No extra batch is needed for the backup collision found by the completion comparison.

- Every one of the 180 test files is assigned exactly once; the assignment lists, inventory
  and checked-out file set agree. No test file was added or removed. Shared evidence and the
  completion check do not change assignment ownership.
- Inventory: 3230 to 3237 collected cases, 1682 to 1680 functions. Nine distinct parameters
  were added: three restore refusals, three registry collision boundaries, two secret-like
  input boundaries and malformed argument masking. Two functions were removed: the workspace
  flag assertion in batch 3 and the fake echo test in batch 10. Their surviving protection is
  named in those records. Counts are the result of those decisions, not an acceptance target.
- Raw ID reconciliation contains eight absent old IDs and fifteen new IDs. Besides the two
  removals, these are the backup, infrastructure, two Card and argument-masking function
  renames/expansions, plus the None-to-no-request access parameter rename. Batches 1–4 and 10
  explain every mapping. Existing flat argument assertions survive in nested-json; the added
  malformed-json case protects a different parse path.
- All 206 opt-in IDs and their gates are unchanged. No live Telegram/provider call or downloaded
  PII-model execution was performed. Real model decisions and recognition quality remain
  outside the local evidence.
- Every accepted snapshot has identical application/example/settings/dependency hashes.
  No product source, prompt, tool description, schema or approved scenario changed. Faults
  were process-local and are absent from the committed tree.
- Final coverage executes 12 additional lines and seven additional branches versus baseline;
  the single lost fake close line is explained above. The Check-review line 119 trace anomaly
  recorded in batches 2, 5 and 7 is not counted as improved protection. The earlier backup
  collision loss was corrected with explicit state and time, not waived.

Representative detection evidence spans the required invariants:

| Invariant | Recorded evidence |
|---|---|
| Access and SQL isolation | Batch 2 foreground/locked hooks/scoped Advisor; batch 10 reader allowlists and nested CTE scope |
| Cancellation and currentness | Batch 2 child cleanup, stale leases and publication; batches 5, 8 and 9 stale review, memory, Home and Key Action results |
| Atomicity and repeated effects | Batch 1 pair replacement/refusal; batch 3 all-or-none Saves, stale/late decisions; batch 7 failed Cue persistence and retry |
| Accounting and frozen state | Batches 4, 6 and 9 hierarchy, occurrence quotas and repeat transfer; batch 8 unchanged frozen retro |
| Privacy and kept conversation | Batch 10 finder failure, nested/raw malformed argument masking and cleared read results; batch 2 access before media handling |

The records distinguish faults already caught from faults that required stronger assertions.
They sample plausible failures rather than claim an exhaustive mutation score. Remaining
coverage gaps are retained guards, opt-in paths and named limits; missing execution did not
justify deleting application code. No unresolved coverage loss is hidden by changing settings
or resetting the baseline. The local final ID/coverage reconciliation is preserved in
.test-audit/quality-final-reconciliation.json; full contexts and reports remain with snapshots.

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
