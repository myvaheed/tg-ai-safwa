# Diary scenarios and Phase 3 audit

Phase 3 covers one Diary entry per local calendar date, its CRUD operations, mood, the Diary
subagent, proposals, and the read-only citation screen. The nightly system Reminder and profile
settings stay in Phase 4. Generic agent suspension and resumption stay in Phase 7.

The owner approved this Gate A package on 2026-08-21, including Advisor-owned reads.

## Decision recorded

`DI-READ-006` records a source conflict:

- `archived_docs/DIARY_PLAN.md` Rules 2–3 say that the Diary subagent is the only reader and every
  Diary request must route to it.
- `CLAUDE.md` says that a subagent owns writes, never reads; the Advisor reads every `ai_*` view.
- `tests/e2e/test_diary_e2e.py::test_the_advisor_reads_a_day_itself_and_cites_it` confirms the
  second behaviour.

The owner chose the current implementation: the Advisor reads and cites Diary days without
routing. The older routed-read rule is superseded by `DI-READ-006`.

## Scenarios

### DI-DAY-001 — Writing a missing day creates its single Diary entry

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Rules 1 and 4
Supersedes: `tests/test_diary.py::test_preparation_settles_a_written_day_on_create_or_update`
(`business_valid`)

Given no Diary entry exists for a local calendar date
When the owner asks Safwa to write that day and approves the proposal
Then one Diary entry is created for that date
And it contains the complete proposed text in the owner's voice
And its mood is the proposed optional feeling score

### DI-DAY-002 — Writing an existing day replaces the whole day

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Rules 1 and 4
Supersedes: `tests/test_diary.py::test_a_day_holds_one_entry_and_a_later_write_replaces_it`
(`business_valid`),
`tests/e2e/test_diary_e2e.py::test_a_second_day_written_the_same_day_overwrites_rather_than_adding`
(`business_valid`)

Given a Diary entry already exists for a local calendar date
When the owner approves another write for that date
Then the existing entry is replaced in full
And no second entry is created for the date
And the existing entry keeps its identity and advances its version

### DI-DATE-003 — A Diary write uses the local date the owner meant

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Rule 1
Supersedes: `tests/e2e/test_diary_e2e.py::test_a_back_dated_day_lands_on_the_day_the_subagent_chose`
(`business_valid`)

Given the owner names a day other than today
When the Diary subagent prepares the write
Then it uses that local calendar date
And it reads the existing entry for that date before proposing the complete replacement

### DI-MOOD-004 — A feeling score is optional and ranges from zero to ten

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Rule 5
Supersedes: `tests/test_diary.py::test_a_feeling_score_runs_from_zero_to_ten` (`business_valid`),
`tests/test_diary.py::test_the_scale_is_stated_once_and_the_model_never_reaches_for_zero`
(`implementation_coupled`)

Given the sources show how the day felt to the owner
When the Diary subagent proposes the day
Then it may include one integer feeling score from 1 through 10
And it omits the score when the sources show no mood
And it uses 0 only when the owner explicitly asks for 0

### DI-DELETE-005 — Removing a day requires an existing entry

Status: approved
Sources: `archived_docs/DIARY_PLAN.md`, decision “mode=update for a day whether or not it exists”
Supersedes: `tests/test_diary.py::test_removing_a_day_that_was_never_written_is_refused`
(`business_valid`), `tests/e2e/test_diary_e2e.py::test_a_removal_deletes_the_day`
(`business_valid`),
`tests/e2e/test_diary_e2e.py::test_removing_a_day_that_was_never_written_is_refused_and_retryable`
(`business_valid`)

Given a Diary entry exists for a local calendar date
When the owner approves removing that date
Then the entry is deleted

Given no Diary entry exists for the date
When the Diary subagent asks to remove it
Then preparation refuses the change as `target_not_found`
And the refusal is retryable
And no proposal is created

### DI-READ-006 — The Advisor reads a Diary day and cites it without routing

Status: approved
Sources: conflict between `archived_docs/DIARY_PLAN.md` Rules 2–3 and `CLAUDE.md` “A session is
the unit”
Supersedes: `tests/test_diary.py::test_both_readers_are_told_about_the_diary_view`
(`implementation_coupled`),
`tests/e2e/test_diary_e2e.py::test_the_advisor_reads_a_day_itself_and_cites_it` (`contradictory`)

Given the owner asks what was written on a Diary day
When the Advisor handles the request
Then the Advisor reads the day from `ai_diary`
And it does not route to the Diary subagent
And it cites the saved entry as `[dd.mm.yyyy](diary:id)`

### DI-LINK-007 — A Diary citation opens the complete read-only day

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Deferred; `archived_docs/ARCHITECTURE.md` Diary screen

Given the Advisor returned a valid `[dd.mm.yyyy](diary:id)` citation
When the owner opens it
Then Safwa shows the full saved text for that day
And the heading contains the local date and optional feeling score
And the screen offers no Diary editing controls

### DI-WRITE-008 — Every AI Diary write is a proposal owned by the Diary subagent

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Rules 2 and 6; `CLAUDE.md` “AI mutations are always
proposals”
Supersedes: `tests/test_diary.py::test_a_written_day_carries_its_text_and_a_removal_carries_none`
(`business_valid`),
`tests/test_diary.py::test_the_call_says_what_was_asked_for_and_nothing_about_the_data`
(`business_valid`),
`tests/e2e/test_diary_e2e.py::test_a_routed_day_travels_from_the_subagent_to_a_saved_entry`
(`business_valid`)

Given the owner asks Safwa to create, replace, or remove a Diary day
When the Advisor handles the request
Then it routes the write to the Diary subagent
And the subagent proposes one `diary` mutation
And no Diary row changes before Save
And Save applies the same Diary operation exposed by the feature
And Discard leaves the Diary unchanged

### DI-RECEIPT-009 — Continuing after Save or Discard does not copy the day's text

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Rule 7
Supersedes: `tests/test_diary.py::test_a_diary_receipt_names_the_day_and_never_repeats_it`
(`implementation_coupled`),
`tests/e2e/test_diary_e2e.py::test_a_resolved_diary_change_hands_back_the_day_shape_and_not_its_text`
(`implementation_coupled`)

Given the Diary subagent is waiting for the owner to Save or Discard its proposal
When the owner makes that decision
Then the result passed back to the Diary subagent and Advisor identifies the date and operation
And it may include the feeling score and character count
And it does not contain the day's text

### DI-OPEN-010 — The Advisor opens a Diary day directly by its date

Status: approved
Sources: owner decision on 2026-08-21; `CLAUDE.md` “A session is the unit”; existing `open` tool
Supersedes: none (`missing`)

Given the owner asks to open the Diary entry for a local calendar date
When an entry exists for that date
Then the Advisor finds its id through `ai_diary`
And it calls `open(item_type="diary", id=...)` without routing
And Safwa opens the complete read-only Diary screen

Given the owner asks to open the Diary entry for the current local date
When no entry exists for that date
Then the Advisor does not call `open`
And it does not route to the Diary subagent
And it tells the owner that there is no Diary entry for that date

### DI-DAY-011 — A day with nothing written is never saved

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Rule 1 (`pov` is the day itself)
Supersedes: none (`missing`)

Given a Diary write whose text is empty or only whitespace
When that day is created or replaced with it
Then the write is refused, and a day that was already written keeps the text it had

The tool contract refuses the same write earlier, before a day is resolved. That is the same rule
at a second door, not a second rule: `DiaryToolInput` is already covered by DI-MOOD-004's
validation test, and the outcome the business cares about — nothing is saved — is what the
operation guarantees.

Primary test: `test_di_day_011_a_day_with_no_words_is_never_saved`.

### DI-DATE-012 — Today is the owner's local day, not the process day

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Rule 1; owner decision on 2026-08-22
Supersedes: none (`missing`)

Given the owner names no date
When the Diary subagent decides which day it is writing, and reads that day's conversation
Then the day is today in the workspace timezone, which past midnight UTC is not the UTC date

Primary tests: `test_di_date_012_today_is_the_local_day`, and
`test_di_date_012_read_day_defaults_to_the_local_day`.

### DI-READ-013 — Neither source alone can write a day, so the subagent holds both

Status: approved
Sources: `archived_docs/DIARY_PLAN.md` Rule 2; `archived_docs/MEMORY_HISTORY_USAGE.md`
Supersedes: none (`missing`)

Work done with buttons never reaches the conversation, and how a day felt never reaches the
planning database. A day written from one source alone is therefore missing half of itself, so
the Diary subagent holds both readers: `read_day` for that day's conversation, and `query_safwa`
over the views its prompt lists. It holds no third read tool.

Primary test: `test_di_read_013_the_subagent_reads_both_sources`.

### DI-MOOD-014 — A rewrite that names no feeling score keeps the saved one

Status: approved
Sources: owner decision on 2026-08-22, resolving the conflict between
`archived_docs/DIARY_PLAN.md` Rule 5 (a score is optional) and the whole-entry replacement of
DI-DAY-002

Given a Diary entry with a feeling score
When that day is rewritten and no feeling score is named
Then the day carries its new text and the score it already had
And a rewrite that names a score stores that score instead

`pov` is replaced whole; `feeling_score` is the one exception, because omitting it is not the
owner asking to erase it. The model omits the score whenever the day "left no sign at all of how
it felt", which is a statement about that day's evidence rather than about the score the owner
already has. Only the owner asking for a different score changes it.

There is deliberately no signal that clears a score back to none. Nothing asked for one, and a
Diary day that once had a mood and then has none is not a case the product has.

The rule is resolved in `DiaryProposalHandler._resolve_day`, where the saved day is already
loaded, so the review screen and the receipt show the score Save will actually store.
`update_diary_entry` stays a plain whole replacement and never has to tell an omitted score from
a deliberate one.

Primary test: `test_di_mood_014_an_unnamed_score_keeps_the_saved_one`.

### DI-READ-015 — A day nobody talked about reads as empty, not as a failure

Status: approved
Sources: owner decision on 2026-08-22, Phase 4.a review
Supersedes: none (`missing`)

Given the owner said nothing to Safwa on a day
When `read_day` is called for that day
Then it returns that day, saying its conversation holds nothing

A silent day is the ordinary case for a Diary written from the database — the owner moved Cards
and never typed. If the reader failed instead, the subagent would lose the day it could still
write. This is a separate rule from DI-READ-013, which is about which readers exist at all.

Primary test: `test_di_read_015_a_silent_day_reads_as_empty`.

## Existing-test audit

The approved traceability contract is [`tests/brd/diary.feature`](../../tests/brd/diary.feature).
Each scenario carries its approved `DI-*` ID; the unit and E2E pytest tests below cite that ID and
the feature file in their docstrings. The feature file has no Behave runner.

| Scenario ID | Existing tests | Class | Decision | Technical regression | Status |
|---|---|---|---|---|---|
| DI-READ-006 | `test_diary.py::test_both_readers_are_told_about_the_diary_view` | `implementation_coupled` | Replace prompt-string assertions after the read owner is approved | `test_di_read_006_advisor_reads_and_cites_day` | replaced; green |
| DI-WRITE-008 | `test_diary.py::test_a_written_day_carries_its_text_and_a_removal_carries_none` | `business_valid` | Keep the validation edge under a scenario ID | `test_di_write_008_write_and_delete_inputs_are_distinct` | replaced; green |
| DI-MOOD-004 | `test_diary.py::test_a_feeling_score_runs_from_zero_to_ten` | `business_valid` | Rewrite with the scenario ID | `test_di_mood_004_score_is_optional_and_bounded` | replaced; green |
| DI-MOOD-004, DI-LINK-007 | `test_diary.py::test_the_scale_is_stated_once_and_the_model_never_reaches_for_zero` | `implementation_coupled` | Split prompt wording from observable label behaviour | `test_di_mood_004_zero_requires_owner_words`; `test_di_link_007_heading_shows_optional_mood` | replaced; green |
| DI-WRITE-008 | `test_diary.py::test_the_call_says_what_was_asked_for_and_nothing_about_the_data` | `business_valid` | Rewrite against the Diary feature contract | `test_di_write_008_update_action_is_resolved_from_live_day` | replaced; green |
| DI-DAY-001, DI-DAY-002 | `test_diary.py::test_preparation_settles_a_written_day_on_create_or_update` | `business_valid` | Split create and replace outcomes | `test_di_day_001_missing_day_is_created`; `test_di_day_002_existing_day_is_replaced` | replaced; green |
| DI-DELETE-005 | `test_diary.py::test_removing_a_day_that_was_never_written_is_refused` | `business_valid` | Rewrite with the scenario ID | `test_di_delete_005_missing_day_is_refused` | replaced; green |
| DI-RECEIPT-009 | `test_diary.py::test_a_diary_receipt_names_the_day_and_never_repeats_it` | `implementation_coupled` | Replace private helper coverage with public proposal-result coverage | `test_di_receipt_009_result_omits_day_text` | replaced; green |
| DI-DAY-001, DI-DAY-002, DI-DELETE-005 | `test_diary.py::test_a_day_holds_one_entry_and_a_later_write_replaces_it` | `business_valid` | Split CRUD outcomes and call public feature operations | three scenario tests above | replaced; green |
| Phase 4 | `test_diary.py::test_settings_is_the_only_source_of_the_diary_reminder` | `business_valid` | Keep unchanged; profile and Reminder behaviour is outside Phase 3 | unchanged | retained |
| Phase 4 | `test_diary.py::test_the_extra_instruction_reaches_the_reminder_text` | `business_valid` | Keep unchanged; profile and Reminder behaviour is outside Phase 3 | unchanged | retained |
| Phase 4 | `test_diary.py::test_the_diary_reminder_is_not_the_owners_to_edit` | `business_valid` | Keep unchanged; Reminder ownership is outside Phase 3 | unchanged | retained |
| DI-WRITE-008 | `e2e/test_diary_e2e.py::test_a_routed_day_travels_from_the_subagent_to_a_saved_entry` | `business_valid` | Rewrite name and trace it to the scenario | `test_di_write_008_routed_write_is_saved_through_proposal` | replaced; green |
| DI-DAY-002 | `e2e/test_diary_e2e.py::test_a_second_day_written_the_same_day_overwrites_rather_than_adding` | `business_valid` | Keep as the primary replacement E2E | `test_di_day_002_second_write_replaces_the_day` | replaced; green |
| DI-DATE-003 | `e2e/test_diary_e2e.py::test_a_back_dated_day_lands_on_the_day_the_subagent_chose` | `business_valid` | Keep and add the scenario ID | `test_di_date_003_named_day_is_used` | replaced; green |
| DI-DELETE-005 | `e2e/test_diary_e2e.py::test_a_removal_deletes_the_day` | `business_valid` | Keep and add the scenario ID | `test_di_delete_005_existing_day_is_deleted` | replaced; green |
| DI-DELETE-005 | `e2e/test_diary_e2e.py::test_removing_a_day_that_was_never_written_is_refused_and_retryable` | `business_valid` | Keep and add the scenario ID | `test_di_delete_005_missing_day_is_retryable` | replaced; green |
| Phase 7 | `e2e/test_diary_e2e.py::test_a_correction_reaches_the_session_that_wrote_the_refused_day` | `characterization_valid` | Keep unchanged; generic agent resumption is outside Phase 3 | unchanged | retained |
| Phase 7 | `e2e/test_diary_e2e.py::test_words_over_a_screen_end_the_caller_but_not_the_draft` | `characterization_valid` | Keep unchanged; generic cancellation is outside Phase 3 | unchanged | retained |
| Phase 7 | `e2e/test_diary_e2e.py::test_a_refused_day_is_over_once_the_advisor_answers_something_else` | `characterization_valid` | Keep unchanged; generic grace rules are outside Phase 3 | unchanged | retained |
| Phase 7 | `e2e/test_diary_e2e.py::test_a_screen_still_open_keeps_its_session_restorable` | `characterization_valid` | Keep unchanged; generic restoration is outside Phase 3 | unchanged | retained |
| DI-READ-006 | `e2e/test_diary_e2e.py::test_the_advisor_reads_a_day_itself_and_cites_it` | `contradictory` | Keep after the owner approved Advisor-owned reads | `test_di_read_006_advisor_reads_and_cites_day` | approved; green |
| DI-RECEIPT-009 | `e2e/test_diary_e2e.py::test_a_resolved_diary_change_hands_back_the_day_shape_and_not_its_text` | `implementation_coupled` | Replace private helper coverage with public proposal-result coverage | `test_di_receipt_009_result_omits_day_text` | replaced; green |
| DI-OPEN-010 | `test_telegram_item_ui.py::test_a_diary_citation_is_named_by_the_entry_and_opens_the_whole_day` | `characterization_valid` | Keep the screen contract; add Advisor-level date lookup and direct-open coverage | `test_di_open_010_advisor_opens_day_without_routing`; `test_di_open_010_missing_day_is_reported_without_routing` | added; green |

## Gate A approval

The owner approved `DI-DAY-001` through `DI-OPEN-010` on 2026-08-21 and selected Advisor-owned
reads for `DI-READ-006`.

`DI-DAY-011`, `DI-DATE-012`, `DI-READ-013` and `DI-READ-015` were approved on 2026-08-22 in the
Phase 4.a review: each documents behaviour the code and the product spec already agree on and
that no test covered.

`DI-MOOD-014` was the only product decision in the packet and shipped as a `question` rather than
being settled by whichever test someone wrote first. The owner approved it on 2026-08-22: an
omitted score keeps the saved one.

`DI-READ-015` began as a second Scenario block under `DI-READ-013` and was split out in the same
review. Which readers the subagent holds and how an empty read behaves are two rules, and one
identifier over both hides the second. Two blocks under one identifier stay only when they are
two observable cases of the *same* question — `DI-DELETE-005` and `DI-OPEN-010` are that, and
`DI-DATE-012` is the same rule at two doors.

## Gate B and Gate C result

The audited Phase 3 scenario contract is
[`tests/brd/diary.feature`](../../tests/brd/diary.feature). The pytest tests named in the audit
trace back to it through their `DI-*` docstrings; they are the executable verification. The Diary
owns its model, use cases, agent contract, proposal adapter, Telegram rendering and SQL view
under `src/safwa/features/diary/`. Both AI writes and direct test/UI callers use the same feature
operations. The database schema, assembled prompt and tool schemas remain unchanged.
