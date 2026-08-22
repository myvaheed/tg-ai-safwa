# Reminders — approval packet

Status: **approved by the owner on 2026-08-22**, Q1, Q2 and Q3 settled with it
Batch: Phase 4.b, split into 4.b.1 and 4.b.2
Sources: `archived_docs/REMINDERS_PLAN.md` (rules 1–10, the pipeline, the parameter table, the
gate, catch-up, deletion, the AI and UI surfaces), `archived_docs/INITIAL_PLAN.md` (a proactive
message only because a Reminder fired), current code and tests.

This file is the approval artifact for the Reminders migration batch. The accepted scenarios move
to `tests/brd/reminders.feature` **as each batch writes its tests** — an approved scenario with no
test fails `tests/test_brd_traceability.py`, so Package B's scenarios enter that file in 4.b.2, not
before.

## The batch is split in two

Reminders is one feature but two coherent halves, and one packet of 24 scenarios is over the
five-to-fifteen size the process asks for. Proposed split — each half ends green, with the bot
working and no old path running beside a new one:

| Batch | Question it answers | Scenarios |
|---|---|---|
| 4.b.1 | What a Reminder is and how one is written | RM-SCHEDULE-001 … RM-WRITE-010 (10) |
| 4.b.2 | What happens when one comes due | RM-FIRE-011 … RM-READ-024 (14) |

If the owner prefers one batch, the scenarios stand as written and only the ordering changes.

## Scope

In: the Reminder record, schedule resolution and arithmetic, the proposal path, the poll, the
escalation turn, catch-up, startup reconciliation, the `/reminders` screens, `ai_reminders`.

Out, and why:

- **Profile's Diary trigger.** `PS-DIARY-012` and `PS-DIARY-013` are approved and own it. This batch
  only keeps `features/reminders/api.py` as the door they call.
- **The Sprint's own end Reminders.** They are Planning behaviour on a Reminders row; see Q2.
- **Re-snapshotting a requeued proposal.** `ReminderProposalHandler.version_model = None` is the
  Phase 2 deferral, and Phase 6 owns it.
- **The Settings screen and the shared handlers.** Phase 8 moves them, whole.

Every number below is written out with its constant named next to it, and the tests read the
constant rather than the literal — see [README.md](README.md#numbers).

---

# Package A — authoring a Reminder (4.b.1)

### RM-SCHEDULE-001 — Timing reaches the system as free text and is computed in code

Status: draft
Sources: REMINDERS_PLAN rules 4 and 5, "The setup mini-session"

```gherkin
Given the board subagent proposes a Reminder with when in plain words
When the proposal is prepared
Then a setup session resolves those words into schedule parameters
And the schedule itself is computed from those parameters in code
And the mutation tool accepts no schedule field of any kind
```

### RM-SCHEDULE-002 — A phrase that does not determine a schedule asks the owner

Status: draft
Sources: REMINDERS_PLAN rule 4, "not_clear_enough"
Supersedes: `tests/e2e/test_reminder_e2e.py::test_an_unresolvable_phrase_becomes_a_retryable_tool_error`
(characterization_valid)

```gherkin
Given the owner says "remind me every morning"
When the setup session runs
Then it answers that the phrase does not determine a schedule, with one question to ask
And that question comes back to the model as a retryable tool error
And no proposal row and no Reminder exist
```

### RM-SCHEDULE-003 — A date is always a start date; a time is a fire clock only next to weekdays

Status: draft
Sources: REMINDERS_PLAN "What `date` and `time` mean depends on what they sit next to" (the table)

```gherkin
Given resolved parameters
When the schedule is built
Then an interval alone starts now and repeats
And an interval with a time starts at that clock's next occurrence
And an interval with a date and a time starts at that exact moment
And weekdays with a time fire weekly at that clock, or daily when all seven are given
And weekdays with a date fire on the first matching day on or after it
And a date with a time is a single occurrence
And a time alone is its next occurrence
```

### RM-SCHEDULE-004 — A recurrence that started in the past is already running, and backfills nothing

Status: draft
Sources: REMINDERS_PLAN "A past start on a recurring schedule is not an error"

```gherkin
Given a repeating schedule whose start is in the past
When its first fire is computed
Then it is the next occurrence from now
And no missed occurrence is created
```

```gherkin
Given a single occurrence whose moment has already passed
When the schedule is built
Then it is refused, because that moment cannot be satisfied
```

### RM-SCHEDULE-005 — Parameters that cannot become a schedule are named, never corrected

Status: draft
Sources: REMINDERS_PLAN "Reject: …"
Supersedes: `tests/test_reminders.py::test_unresolvable_configurations` (business_valid, kept)

```gherkin
Given parameters that contradict each other or fall outside the allowed range
When the schedule is built
Then the reason is stated and no schedule is produced
And an interval below 5 minutes (REMINDER_MIN_INTERVAL_MINUTES), weekdays given together with
    an interval, quiet windows on a schedule that is not an interval, quiet windows leaving no
    time of day, a date without an hour, and nothing at all are each such a reason
```

### RM-CLOCK-006 — A stored fire time is a local wall clock, not a UTC offset

Status: draft
Sources: REMINDERS_PLAN "Step 2 is redone on every call and never cached"

```gherkin
Given a daily Reminder at 08:30 local
When the local zone crosses a daylight-saving shift
Then the next fire is still 08:30 local
And a quiet window edge lands at the same local clock on both sides of the shift
```

### RM-QUIET-007 — A quiet window suppresses hours of the day, and its end is exclusive

Status: draft
Sources: REMINDERS_PLAN "A window whose start is later than its end wraps midnight"

```gherkin
Given an interval Reminder with a quiet window
When a computed fire lands inside that window
Then it moves to the window's end
And a window written as one range wrapping midnight and the same window written as two ranges
    meeting at 00:00 behave identically
And chained windows push a candidate through all of them
```

### RM-WRITE-008 — No Reminder is written without a proposal, and Save writes what the screen showed

Status: draft
Sources: REMINDERS_PLAN rule 5, "Then: an ordinary proposal"
Supersedes: `tests/e2e/test_reminder_e2e.py::test_a_reminder_reaches_a_proposal_and_save_creates_the_row`,
`::test_discarding_the_proposal_leaves_no_reminder`,
`::test_the_proposal_carries_the_resolved_schedule_not_the_words` (all business_valid, kept)

```gherkin
Given the model proposes a Reminder
When the owner sees the review screen
Then it names the resolved schedule rather than the words the owner used
And Save creates the Reminder with exactly that schedule and its first fire
And Discard leaves no Reminder and no trace of one
```

### RM-WRITE-009 — Editing the text never moves the schedule

Status: draft
Sources: REMINDERS_PLAN rule 10, UI surface
Supersedes: `tests/e2e/test_reminder_e2e.py::test_editing_a_reminder_without_when_never_touches_the_schedule`,
`tests/test_reminder_flow.py::test_editing_text_leaves_the_schedule_alone` (business_valid, kept),
`tests/test_telegram_item_ui.py::test_reminder_text_requires_a_value_and_restores_its_view`
(characterization_valid)

One rule at two doors.

```gherkin
Given a Reminder the model is editing
When it sends new instruction text and no timing
Then no setup session runs and no schedule column changes
```

```gherkin
Given the owner opens a Reminder and edits its text
When they send the new text
Then the Reminder keeps its next fire
And empty text is refused and the Reminder view comes back unchanged
```

### RM-WRITE-010 — Deletion is the only off switch

Status: draft
Sources: REMINDERS_PLAN "Deletion", data model "Four deliberate absences"

```gherkin
Given a Reminder
When it is removed, by the owner from its screen or by an approved model proposal
Then the row is gone and it stops firing immediately
And there is no archive and no disabled state to return from
And removal is one confirmation, not the permanent-deletion screen a Card tree gets
```

---

# Package B — a Reminder coming due (4.b.2)

### RM-FIRE-011 — A fired Reminder hands its text to the Advisor as a request; the system composes nothing

Status: draft
Sources: REMINDERS_PLAN rules 1, 3 and 6, "What the advisor receives"
Supersedes: `tests/test_reminder_flow.py::test_one_firing_reads_as_one`, `::test_a_batch_is_numbered`,
`::test_the_main_advisor_is_told_to_verify_named_items_first`, `::test_a_late_firing_says_how_late`,
`::test_an_on_time_firing_says_nothing_about_lateness`, `::test_the_fire_history_is_carried_over`,
`::test_reminder_advisor_receives_canonical_dialogue` (business_valid, kept)

```gherkin
Given a Reminder comes due
When it is escalated
Then the Advisor receives the instruction text, its schedule and its firing history as one request
And it reads the same canonical dialogue an owner message would
And its answer is registered as a proactive bot message, not as a reply
And nothing between the poll and the Advisor decides what the Reminder means
```

### RM-FIRE-012 — One poll is one turn

Status: draft
Sources: REMINDERS_PLAN rule 7, "The loop"
Supersedes: `tests/test_scheduler.py::test_one_escalation_carries_at_most_the_batch_size`,
`::test_the_oldest_due_reminders_go_first` (business_valid, kept)

```gherkin
Given 5 Reminders are due at the same poll
When the poll runs
Then the 3 oldest go to the Advisor as one request (REMINDER_FIRE_BATCH = 3)
And the other 2 stay due, for the next poll 30 seconds later (SCHEDULER_POLL_SECONDS = 30)
```

### RM-FIRE-013 — A schedule advances only after the answer was delivered

Status: draft
Sources: REMINDERS_PLAN "The loop", "Blocked escalations are not queued anywhere"
Supersedes: `tests/test_scheduler.py::test_a_closed_gate_advances_nothing`,
`::test_a_failed_escalation_advances_nothing`, `::test_settle_records_a_successful_delivery`
(business_valid, kept)

```gherkin
Given a due Reminder
When the gate is closed, or the turn fails, or the owner takes the lease mid-turn
Then no schedule is advanced and the Reminder is still due at the next poll, 30 seconds later
    (SCHEDULER_POLL_SECONDS = 30)
And only a delivered answer advances it, records the firing and counts it
```

### RM-FIRE-014 — Advancing a schedule is bookkeeping, not an owner-visible change

Status: draft
Sources: REMINDERS_PLAN "Advancing `next_fire_at` does not bump `workspace.revision`"

```gherkin
Given an answer was delivered and the schedules advance
When the workspace revision is read
Then it is unchanged, so no pending proposal and no in-flight answer is invalidated by a fire
```

The next scenario is why this one is separate: a Reminder the owner or the model *writes* does bump
the revision, and only the scheduler's own advance does not.

### RM-FIRE-015 — A repeat advances from its scheduled moment, not from when the answer arrived

Status: draft
Sources: REMINDERS_PLAN "The full pipeline"
Supersedes: `tests/test_scheduler.py::test_a_repeat_advances_from_its_scheduled_moment_not_the_delivery_moment`
(business_valid, kept)

```gherkin
Given a repeating Reminder due at 08:30 and a turn that takes four minutes
When the answer is delivered
Then the next fire is computed from 08:30
And a slow turn does not push every later fire out
```

### RM-FIRE-016 — A single occurrence fires exactly once, however late

Status: draft
Sources: REMINDERS_PLAN "Catch-up after downtime", "Deletion"
Supersedes: `tests/test_scheduler.py::test_a_one_shot_is_deleted_only_after_the_turn_succeeds`,
`::test_a_one_shot_fires_however_late` (business_valid, kept)

```gherkin
Given a single-occurrence Reminder that is overdue
When the poll finds it
Then it fires, and the request says how late it is
And it deletes itself only after the answer was delivered
And it never produces a second escalation
```

### RM-GATE-017 — Nothing escalates on top of an unanswered question

Status: draft
Sources: REMINDERS_PLAN rule 8, "The gate"

```gherkin
Given a Reminder is due
When the Advisor is generating, or a proposal is pending, or an approval batch or a claimed
    session is still unresolved
Then nothing is escalated and nothing is queued
And the Reminder stays due for the next poll
```

### RM-GATE-018 — The owner always wins

Status: draft
Sources: REMINDERS_PLAN rule 9
Supersedes: `tests/test_reminder_flow.py::test_a_background_lease_is_marked_background`,
`::test_an_owner_lease_is_not_background`, `::test_a_background_lease_never_steals_from_the_owner`,
`::test_releasing_a_background_lease_frees_the_guard`,
`::test_cancelling_a_background_lease_bumps_the_dialogue_revision`,
`::test_releasing_a_background_lease_cannot_free_an_owner_lease`,
`::test_cancelling_a_foreground_lease_aborts_its_task`,
`::test_cancelling_a_background_lease_leaves_its_loop_running` (business_valid, kept as the edge
tests of this scenario)

```gherkin
Given a background escalation holds the generation lease
When the owner sends a message
Then the escalation is cancelled and the owner's message is answered
And its half-finished answer is discarded and nothing is published
And the Reminder was never advanced, so it is still due
```

### RM-CATCHUP-019 — A missed repeat gets at most one catch-up

Status: draft — Q1 settled by the owner on 2026-08-22
Sources: REMINDERS_PLAN "Catch-up after downtime", corrected by this decision
Supersedes: `tests/test_scheduler.py::test_a_repeat_inside_the_grace_window_still_fires`,
`::test_a_repeat_past_the_grace_window_rolls_forward_silently` (business_valid, kept)

The grace is measured from the **stored fire time** — the oldest occurrence that was missed — and
not from the most recent one the schedule would have produced. How long the Reminder went
unanswered is the fact that matters; how often it repeats does not change it.

```gherkin
Given a repeating Reminder whose stored fire time is 90 minutes overdue
When the poll finds it
Then it fires once, and that one fire is the whole catch-up
    (90 is under REMINDER_CATCHUP_GRACE_MINUTES = 120)
```

```gherkin
Given a repeating Reminder whose stored fire time is 3 hours overdue
When the poll finds it
Then its schedule rolls forward silently and the owner is told nothing
And however often it repeats: one every 5 minutes, offline for 3 hours, escalates nothing at
    all rather than 36 times
```

### RM-START-020 — Startup fixes what downtime made wrong, and only that

Status: draft
Sources: REMINDERS_PLAN "Startup"
Supersedes: `tests/test_reminder_flow.py::test_reconcile_leaves_an_in_grace_overdue_repeat_for_the_poll`,
`::test_reconcile_rolls_a_long_overdue_repeat_forward`, `::test_reconcile_never_moves_a_one_shot`,
`::test_reconcile_rebuilds_a_wall_clock_after_a_timezone_move`,
`::test_reconcile_is_a_no_op_when_the_timezone_has_not_moved` (business_valid, kept)

```gherkin
Given the bot starts
When Reminders are reconciled
Then a wall-clock schedule whose stored fire no longer matches its local clock is rebuilt
And a repeat overdue past the grace rolls forward
And a repeat overdue inside the grace is left for the first poll to fire
And a single occurrence is never moved
```

### RM-POLL-021 — The poll outlives its own failures

Status: draft
Sources: `docs/MIGRATION.md` "A background loop swallowed its own death"; REMINDERS_PLAN "The loop"
Supersedes: `tests/test_scheduler.py::test_the_loop_survives_a_failing_tick` (business_valid, kept)

```gherkin
Given one poll raises
When the next poll comes round 30 seconds later (SCHEDULER_POLL_SECONDS = 30)
Then it runs, and the failure was logged rather than lost
And Reminders do not silently stop firing for the life of the process
```

This is the class of failure Phase 4.a shipped: nothing breaks loudly, and the bot keeps answering
while a whole feature is dead.

### RM-SYSTEM-022 — A Reminder no owner set belongs to Safwa

Status: draft — Q2 settled by the owner on 2026-08-22
Sources: REMINDERS_PLAN data model; `PS-DIARY-012` (approved)
Supersedes: `tests/test_telegram_item_ui.py::test_the_reminders_screen_and_settings_hide_safwas_own_reminder`,
`tests/e2e/test_reminder_e2e.py::test_the_diary_reminder_is_invisible_to_the_model` (business_valid,
kept), `tests/test_scheduler.py::test_a_system_reminder_fires_like_any_other` (business_valid, kept)

There are two such Reminders, and they are the same kind of thing: the Diary trigger Settings
derives, and the one or two end warnings a Sprint creates. The owner set neither, and editing
either would be editing a derived row whose author would overwrite it.

```gherkin
Given a Reminder Safwa derived rather than the owner set — the Diary trigger, or a Sprint's own
    end warning
When the owner opens /reminders, or the model reads Reminders
Then it is not there
And every edit, reschedule and delete path refuses it and says where to change it
And it fires exactly like any other Reminder
And the feature that created it is what removes it: Settings for the Diary trigger, finishing
    the Sprint for its end warnings
```

**This half is a behaviour change.** A Sprint's end Reminders are `system = False` today, so the
owner sees them in `/reminders`, may edit or delete them, and the model reads them in
`ai_reminders`. The test is written first and fails; the fix is `system=True` where
`_schedule_sprint_reminders` already sets `sprint_id`. Nothing else moves: `finish_sprint` deletes
by `sprint_id` without going through the refusing path, and `sync_daily_system_reminder` already
selects only the system Reminder that belongs to no Sprint.

### RM-UI-023 — /reminders is a list, a detail and two actions

Status: draft
Sources: REMINDERS_PLAN "UI surface"

```gherkin
Given the owner opens /reminders
Then each Reminder reads as its schedule and the start of its text, next fire first
And opening one shows its schedule, its next fire, its firing history and its full text
And the only actions are editing the text and deleting it
And there is no way to create a Reminder here and no way to edit a schedule here
And an empty list says the advisor is who creates them
```

### RM-READ-024 — The Advisor reads Reminders through the view, never through the prompt prefix

Status: draft
Sources: REMINDERS_PLAN "AI surface" — "Do not inject Reminders into `planning_context`"
Supersedes: `tests/e2e/test_reminder_e2e.py::test_ai_reminders_view_is_readable` (business_valid, kept)

```gherkin
Given Reminders exist
When the model needs to know about them
Then it queries the read-only view, which gives it the schedule columns as they are stored
And the next fire reads as the owner's local wall clock, not as the stored UTC instant
And no Reminder appears in the assembled prompt prefix, whose next fire moves on every fire
```

---

## Questions

None of these is settled by writing a test. Q1 and Q2 were decided by the owner on 2026-08-22 and
are recorded here because the scenarios above now depend on the decision.

### Q1 — Which missed occurrence does the catch-up grace measure? (RM-CATCHUP-019)

**Decided: the stored fire time — the oldest missed occurrence. The code is right and the spec
sentence is corrected.**

`REMINDERS_PLAN` says a missed repeat still fires "only if **the most recent missed occurrence** is
within `REMINDER_CATCHUP_GRACE_MINUTES`". The code measures from `next_fire_at`, which is the
**oldest** missed occurrence.

They differ exactly when the Reminder repeats faster than the downtime. A Reminder every 5 minutes,
offline for three hours: its most recent missed occurrence was 5 minutes ago — the spec says fire
it — while its stored `next_fire_at` is three hours overdue, and the code rolls it forward silently.
A daily Reminder behaves the same either way.

- **A — the code is right.** "Three hours of downtime" is the fact, and a posture Reminder that went
  unanswered for three hours has nothing useful left to say.
- **B — the spec is right.** The catch-up asks "was there a fire I should have had just now", and
  for a frequent Reminder there was. Requires computing the most recent occurrence at or before now.

Chosen: **A**. `archived_docs/REMINDERS_PLAN.md` is corrected in this batch so the two do not
contradict each other; RM-CATCHUP-019 owns the rule from now on.

### Q2 — Are a Sprint's end Reminders ordinary Reminders? (RM-SYSTEM-022)

**Decided: they are system Reminders.**

`start_sprint` creates up to two one-shot Reminders carrying `sprint_id`, and `finish_sprint`
deletes them. They are `system = False`, so today the owner sees them in `/reminders`, may edit or
delete them, and the model reads them in `ai_reminders`. `REMINDERS_PLAN` never mentions them — they
arrived with Sprint.

Chosen: **hidden and refused, like the Diary trigger.** RM-SYSTEM-022 carries the rule and names
the one-line change; RM-UI-023 and RM-READ-024 inherit it.

The *write* still belongs behind `features/reminders/api.py`, next to `sync_daily_system_reminder`,
so Planning says what to say and when and Reminders keeps the row and its arithmetic. That move is
Phase 5's — this batch changes the flag, not the ownership.

### Q3 — What `ai_reminders` gives the model (RM-READ-024)

**Decided: keep the raw schedule columns, and convert `next_fire_at` to local time.**

The two shapes, for the same Reminder:

```text
spec:  id=7  instruction="Ask me what to start with today."
             schedule="every weekday at 08:30"   next_fire_at_local="2026-08-23 08:30"

code:  id=7  instruction="Ask me what to start with today."
             schedule_kind="weekly"  weekdays=["Mon","Tue","Wed","Thu","Fri"]
             at_time="08:30"  interval_minutes=NULL  quiet_windows=[]
             next_fire_at="2026-08-23 05:30"   (UTC)   last_fired_at  fire_count
```

- **A — keep the raw columns (recommended).** `describe()` is the single wording used by the review
  screen, the `/reminders` list and the escalation text. Building `schedule` in SQL would be a
  second implementation of it, in a language that cannot call the first, free to drift. The model
  does not need the phrase: it reads this view to find the id it is about to edit, and the phrase
  reaches the owner through the proposal screen, which renders it with `describe()`.
- **B — follow the spec.** The model gets one readable phrase and a local timestamp, and cannot
  misread `weekdays` plus `at_time`. Costs a `describe()` written a second time in SQL, and every
  future schedule shape has to be added in both.

The one real cost of A is `next_fire_at` in UTC: a model that repeats it to the owner says 05:30
where the owner means 08:30. Two ways to close that without B — either the view converts only that
column to local time, or nothing changes, because the model already reads the timezone from the
prompt and rarely quotes a fire time.

Chosen: **A plus the local-time conversion on that one column.** The column is renamed
`next_fire_at_local` so it is not read as the UTC instant every other `ai_*` column is — it is the
first and only local timestamp in the catalogue, and the name is what says so. `last_fired_at`
stays UTC: nothing asked for it, and the model quotes the next fire, not the last one.

SQLite has no timezone database, so the conversion is a `local_time(…)` function registered on the
read-only connection in `ai/sql.py`, taking the timezone the runner is constructed with. A fixed
offset baked into the view SQL would be wrong for half of every DST year, and `describe()` is not
duplicated either way. `CREATE VIEW` does not resolve the function, so only the read path needs it.

**This is the one snapshot cost of the batch.** The `ai_reminders` column list is prose inside
`SYSTEM_PROMPT` and inside the board subagent's prompt, so both hashes move. It is a declared
change, not volatile context leaking into `messages[0]` — but it does invalidate the Advisor's
cached prompt prefix once, on the deploy that ships it.

### Q4 — Not this batch, recorded so it is not lost

A requeued Reminder proposal is not re-snapshotted against the current row
(`ReminderProposalHandler.version_model = None`, the Phase 2 deferral). Phase 6 owns it.

---

## Audit table

`business_valid` here means the test agrees with the scenario above it and needs only its docstring
and, where the module moves, its import path.

| Scenario | Existing tests | Class | Decision | Status |
|---|---|---|---|---|
| RM-SCHEDULE-001 | `test_reminder_e2e.py::test_the_proposal_carries_the_resolved_schedule_not_the_words` | business_valid | keep, cite | draft |
| RM-SCHEDULE-002 | `test_reminder_e2e.py::test_an_unresolvable_phrase_becomes_a_retryable_tool_error` | business_valid | keep, cite | draft |
| RM-SCHEDULE-003 | `test_reminders.py` — `test_interval_alone_starts_now`, `test_interval_with_time_…`, `test_interval_with_date_and_time_…`, `test_days_and_time_is_weekly`, `test_all_seven_days_is_daily`, `test_date_and_time_is_once`, `test_time_alone_is_once_…`, `test_weekday_names_are_normalized_and_deduplicated`, `test_first_fire_of_an_interval_is_its_anchor`, `test_first_fire_of_a_weekly_…`, `test_a_future_start_holds_a_weekly_back`, `test_a_start_landing_exactly_on_a_matching_slot_is_included` | business_valid | keep as the edge tests, one cites | draft |
| RM-SCHEDULE-004 | `test_reminders.py::test_past_start_on_a_recurrence_is_allowed`, `::test_a_past_start_on_a_recurrence_does_not_backfill`, `::test_a_one_shot_fires_once_and_then_never_again` | business_valid | keep, cite | draft |
| RM-SCHEDULE-005 | `test_reminders.py::test_unresolvable_configurations` (parametrized) | business_valid | keep, cite | draft |
| RM-CLOCK-006 | `test_reminders.py::test_a_wall_clock_survives_a_daylight_saving_shift`, `::test_a_quiet_window_edge_survives_a_daylight_saving_shift` | business_valid | keep, cite | draft |
| RM-QUIET-007 | `test_reminders.py::test_a_candidate_inside_a_quiet_window_moves_to_its_end`, `::test_a_candidate_outside_every_window_is_untouched`, `::test_a_wrapping_window_and_its_split_form_are_the_same_window`, `::test_chained_windows_push_a_candidate_through_all_of_them`, `::test_a_candidate_before_midnight_inside_a_wrapping_window_…` | business_valid | keep, cite | draft |
| RM-WRITE-008 | `test_reminder_e2e.py::test_a_reminder_reaches_a_proposal_and_save_creates_the_row`, `::test_discarding_the_proposal_leaves_no_reminder`; `test_reminder_flow.py::test_create_reminder_computes_its_first_fire`, `::test_create_reminder_rejects_empty_text`, `::test_a_schedule_survives_the_proposal_json_round_trip`, `::test_the_proposal_payload_is_json_serializable`; `test_reminders.py::test_a_schedule_survives_the_column_round_trip` | business_valid | keep, cite | draft |
| RM-WRITE-009 | `test_reminder_e2e.py::test_editing_a_reminder_without_when_never_touches_the_schedule`; `test_reminder_flow.py::test_editing_text_leaves_the_schedule_alone`, `::test_rescheduling_replaces_every_schedule_column`; `test_telegram_item_ui.py::test_reminder_text_requires_a_value_and_restores_its_view` | business_valid | keep, cite (two doors) | draft |
| RM-WRITE-010 | `test_reminder_flow.py::test_deleting_a_reminder_removes_the_row` | business_valid | keep; **missing**: that a Reminder deletion is one Save and not the Card-tree confirmation | draft |
| RM-FIRE-011 | `test_reminder_flow.py` — the six `format_escalation` tests plus `test_reminder_advisor_receives_canonical_dialogue`; `test_scheduler.py::test_a_due_reminder_reaches_the_advisor_without_a_preflight_session` | business_valid | keep, cite | draft |
| RM-FIRE-012 | `test_scheduler.py::test_one_escalation_carries_at_most_the_batch_size`, `::test_the_oldest_due_reminders_go_first` | business_valid | keep, cite | draft |
| RM-FIRE-013 | `test_scheduler.py::test_a_closed_gate_advances_nothing`, `::test_a_failed_escalation_advances_nothing`, `::test_settle_records_a_successful_delivery` | business_valid | keep, cite | draft |
| RM-FIRE-014 | — | missing | new test: a delivered escalation leaves `workspace.revision` alone | draft |
| RM-FIRE-015 | `test_scheduler.py::test_a_repeat_advances_from_its_scheduled_moment_not_the_delivery_moment` | business_valid | keep, cite | draft |
| RM-FIRE-016 | `test_scheduler.py::test_a_one_shot_is_deleted_only_after_the_turn_succeeds`, `::test_a_one_shot_fires_however_late`; `test_reminders.py::test_roll_forward_leaves_a_one_shot_where_it_is` | business_valid | keep, cite | draft |
| RM-GATE-017 | — the gate is exercised only through `test_a_closed_gate_advances_nothing`, which supplies a closed gate rather than testing what closes it | missing | new test: each of generating, a pending proposal, an unresolved approval batch and a claimed run closes the gate | draft |
| RM-GATE-018 | the eight `GenerationGuard` lease tests in `test_reminder_flow.py` | business_valid | keep as edges, cite | draft |
| RM-CATCHUP-019 | `test_scheduler.py::test_a_repeat_inside_the_grace_window_still_fires`, `::test_a_repeat_past_the_grace_window_rolls_forward_silently`; `test_reminders.py::test_roll_forward_skips_every_missed_interval_at_once`, `::test_roll_forward_skips_missed_weekly_occurrences`, `::test_roll_forward_respects_quiet_windows` | business_valid | keep, cite; **missing**: the frequent-interval case Q1 decided — every 5 minutes, 3 hours overdue, escalates nothing | draft |
| RM-START-020 | the five `reconcile_reminders` tests in `test_reminder_flow.py` | business_valid | keep, cite | draft |
| RM-POLL-021 | `test_scheduler.py::test_the_loop_survives_a_failing_tick` | business_valid | keep, cite | draft |
| RM-SYSTEM-022 | `test_telegram_item_ui.py::test_the_reminders_screen_and_settings_hide_safwas_own_reminder`; `test_reminder_e2e.py::test_the_diary_reminder_is_invisible_to_the_model`; `test_scheduler.py::test_a_system_reminder_fires_like_any_other` | business_valid | keep, cite; **missing**: that the edit, reschedule and delete paths refuse it, and that a Sprint's end warnings are system too (fails first — see Q2) | draft |
| RM-SYSTEM-022 | `test_sprint.py::test_starting_a_sprint_schedules_both_end_reminders` | characterization_valid | keep, extend with the system flag | draft |
| RM-UI-023 | `test_telegram_item_ui.py::test_reminder_text_requires_a_value_and_restores_its_view` only | missing | new test: the list, the detail and the delete confirmation | draft |
| RM-READ-024 | `test_reminder_e2e.py::test_ai_reminders_view_is_readable` | business_valid | keep, cite; **missing**: that no Reminder reaches the prompt prefix — today only the byte-stable prefix snapshot would catch it | draft |

Tests in these files that belong to other features and are not touched by this batch:
`test_reminders.py::test_a_settings_clock_reads_a_time_or_the_off_switch` and
`::test_a_settings_clock_rejects_anything_else` (Profile, `PS-DIARY-*`),
`test_scheduler.py::test_memory_update_time_column_accepts_a_time` (Continuity),
`test_profile.py::test_diary_reminder_settings_sync_only_the_diary_system_reminder` and
`::test_startup_reconciles_the_diary_trigger_before_rebuilding_reminders` (`PS-DIARY-012`, `-013`).

Seven gaps. Six are behaviour that exists and nothing checks: RM-FIRE-014, RM-GATE-017, the
refusal half of RM-SYSTEM-022, the screens in RM-UI-023, the one-Save deletion in RM-WRITE-010,
and the prefix half of RM-READ-024. The seventh, the Sprint half of RM-SYSTEM-022, is behaviour
that does not exist yet: its test is written first, fails, and the one-line change makes it pass
inside this batch.

---

## The code move, for approval alongside the behaviour

Nothing here changes behaviour. It is listed because Phase 3 established that the owner approves the
shape of the code as well as the report.

**4.b.1**

| From | To | Note |
|---|---|---|
| `src/safwa/reminders.py` | `features/reminders/schedule.py` | `Schedule` is a public class large enough for its own module, like `memory.py` and `persona.py` |
| `models.Reminder` | `features/reminders/model.py` | `safwa.models` keeps the compatibility import, as Diary did, so startup still sees the whole metadata |
| `domain.create_reminder`, `update_reminder_text`, `reschedule_reminder`, `delete_reminder`, `_editable_reminder` | `features/reminders/use_cases.py` | the caller keeps owning the transaction |
| `ai/reminder_sessions.py` | `features/reminders/agent.py` | next to `REMINDER_TOOL`; the module disappears |

**4.b.2**

| From | To | Note |
|---|---|---|
| `scheduler.py` — `Firing`, `due_reminders`, `is_stale`, `prepare`, `settle`, `tick`, `run_scheduler` | `features/reminders/background.py` | which already holds the task declaration |
| `recovery.reconcile_reminders` | `features/reminders/use_cases.py` | `module.recover` already points at it |
| `telegram/escalation.py` | `features/reminders/telegram.py` | see the judgement call below |
| `scheduler.run_sprint_expiry` | `features/planning/background.py` | its only caller; `scheduler.py` is then empty and deleted |

**Two edits that are not moves**, both decided above:

- `domain._schedule_sprint_reminders` sets `system=True` alongside the `sprint_id` it already sets
  (Q2). One line, in 4.b.2 with RM-SYSTEM-022.
- `archived_docs/REMINDERS_PLAN.md` — the catch-up sentence is corrected to say the grace is
  measured from the stored fire time (Q1), so the spec and RM-CATCHUP-019 do not contradict.

**The one judgement call.** `telegram/escalation.py` registers no handlers and is driven by this
feature's own poll, so it is not a screen and the "do not move the screens" rule does not cover it.
It does import `telegram/_core.py` and `telegram/proposals.py`, and `safwa.telegram` imports nothing
from `features.reminders` once `background.py` stops reaching for `ReminderRuntime` — so the edge
runs one way and no cycle appears. If that reasoning is not wanted this batch, leaving
`telegram/escalation.py` where it is costs nothing and Phase 8 takes it with the rest.

`telegram/reminders.py` — the `/reminders` screens — **stays where it is**. Moving it is what made
`features/profile/screens.py` a cycle, and Phase 8 moves it with the shared handler mechanism.

## Gates

Run and recorded when the batch closes, not now.

```bash
uv run pytest -q
uv run ruff check .
uv run python scripts/architecture_metrics.py
```

Baseline to beat: 540 passed / 3 skipped, DoD #1 28, #2 0, #3 5, #13 0, 445 edges, 0 cycles.

Snapshot hashes this batch may move:

- **4.b.1 — none.** `PERSONA`, `SYSTEM_PROMPT`, `tool:reminder` and `schema.json` all moving would
  each be a red flag; nothing in this half changes a contract the model or the database sees.
- **4.b.2 — `SYSTEM_PROMPT` and the board prompt only**, and only for the `ai_reminders` column
  list Q3 changes. `PERSONA` and `tool:reminder` moving is still a red flag, and so is any other
  line inside the two that do move.
