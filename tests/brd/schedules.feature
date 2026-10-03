Feature: One plain-language Schedule for Actions and independent Checks

  Scenario: SCH-QUOTA-001 — A calendar quota opens one current instance
    Given an Action scheduled five times a day
    When the fifth instance is completed
    Then one next instance belongs to the following local day
    And the plan reports five completed instances in the original day

  Scenario: SCH-WINDOW-002 — A partial week does not allocate its quota to a chosen day
    Given a Check scheduled once a week without a clock
    When the Advisor queries one day of that week
    Then the whole weekly quota is reported with partial coverage
    And no appointment time is invented

  Scenario: SCH-CHECK-003 — A scheduled Check is an independent observation series
    Given a Check with its own Schedule
    When it is answered Missed
    Then the answer counts as an observation and the next instance is opened
    And the Check cannot be attached to a Card

  Scenario: SCH-COMPILE-004 — A changed source invalidates an in-flight compilation
    Given a Schedule is being compiled
    When its text is replaced before compilation finishes
    Then the old result does not overwrite the new revision

  Scenario: SCH-CLARIFY-005 — An ambiguous Schedule asks once per revision
    Given a Schedule whose periodicity is incomplete
    When the Scheduler requests clarification
    Then hourly recovery does not parse the same ambiguous text again

  Scenario: SCH-ZONE-006 — Periods use the workspace calendar
    Given a workspace timezone with daylight saving time
    When a weekly period is calculated
    Then its boundary is local Monday midnight rather than UTC midnight

  Scenario: SCH-DELETE-007 — Deleting the current instance ends its Schedule
    Given a repeating Action or Check with completed observations
    When the owner deletes its current open instance
    Then no future work is reported for that series
    And its retained observations still count in history

  Scenario: SCH-END-008 — A final Action can be reopened after repetition ends
    Given a repeating Action whose current Schedule is cleared or changed to one appointment
    When the final Action is completed and then reopened
    Then it can be used as an ordinary Action
    And earlier completed instances stay closed

  Scenario: SCH-INTERVAL-009 — Early completion consumes its appointment
    Given an Action or Check repeating at a fixed interval
    When its future appointment is completed early
    Then the next appointment is later than the one just completed

  Scenario: SCH-REPAIR-010 — Clear timing does not ask the owner to fix model formatting
    Given a Schedule with an explicit weekday and clock
    When the model supplies the clock in an invalid format
    Then the Scheduler asks the model to repair its arguments
    And a valid repair configures the Schedule without a question to the owner

  Scenario: SCH-READ-011 — A malformed schedule query can be corrected in the same turn
    Given the Advisor asks for scheduled work with invalid dates or type
    When the read request is rejected
    Then the Advisor receives a precise correction instruction
    And can retry and answer in the same turn

  Scenario: SCH-SUMMARY-012 — One series has one compact progress summary
    Given scheduled Actions or Checks with observations before and during the current Sprint
    When the Advisor reads their plan
    Then each series appears once with its next event and selected-range, Sprint and lifetime progress
    And Passed and Missed remain distinct within answered Checks
    And an unlimited series has no invented lifetime remainder
    And large results can be continued without losing a series
