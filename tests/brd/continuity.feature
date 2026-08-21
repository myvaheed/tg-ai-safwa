Feature: Continuity summaries and memory
  Safwa keeps dialogue summaries current and treats memory.md as owner-editable text.

  Scenario: CO-SUMMARY-001 - Automatic Summary waits for 6000 estimated tokens
    Given canonical dialogue is below 6000 estimated tokens
    Then the automatic post-turn check writes no Summary
    But an explicit forced check may write one

  Scenario: CO-SUMMARY-002 - A new Summary includes the previous Summary
    Given a previous Summary and newer canonical dialogue
    When another Summary is requested
    Then the provider input contains the previous Summary and the newer dialogue

  Scenario: CO-SUMMARY-003 - An owner message arriving during Summary generation wins the race
    Given Summary generation has read one canonical dialogue snapshot
    When an owner message arrives before the generated Summary is recorded
    Then the result based on the earlier snapshot is discarded
    And a later attempt may summarize the expanded dialogue

  Scenario: CO-MEMORY-004 - memory.md is the source of durable facts
    Given memory.md is an ordinary UTF-8 text file
    When memory is read
    Then trimmed non-empty lines are facts in file order
    And blank lines are ignored
    And an absent file means no facts

  Scenario: CO-MEMORY-005 - A later local edit is observed on the next synchronization
    Given Safwa has already read memory.md
    When the owner edits the file outside Safwa
    Then the next synchronization returns the edited facts

  Scenario: CO-MEMORY-006 - An AI replacement cannot overwrite a newer local edit
    Given AI maintenance started from one file hash
    When the owner edits memory.md before replacement
    Then replacement is rejected
    And the owner's file remains byte-for-byte unchanged

  Scenario: CO-SYNC-007 - Memory maintenance starts after its own cursor
    Given memory maintenance previously processed dialogue to a cursor
    When maintenance reads again
    Then it requests canonical dialogue after that cursor

  Scenario: CO-SYNC-008 - The memory cursor advances only after a successful file write
    Given canonical dialogue exists after the memory cursor
    When an owner file edit makes the generated replacement lose its hash race
    Then the owner's newer memory.md is preserved
    And the processed cursor is unchanged

  Scenario: CO-SCHEDULE-009 - Configured Memory maintenance runs once per local day
    Given Memory sync time is due
    And no successful run is recorded for the local day
    When the scheduler checks more than once that day
    Then maintenance runs exactly once

  Scenario: CO-SCHEDULE-010 - off disables scheduled Memory maintenance
    Given Memory sync time is off
    When the scheduler checks eligibility
    Then maintenance does not run

  Scenario: CO-GENERATION-011 - Foreground dialogue has priority over Summary and Memory
    Given Summary and Memory use the same background-generation gate
    When foreground dialogue generation is active
    Then neither background operation starts
    And a dialogue revision change marks an already running result stale
