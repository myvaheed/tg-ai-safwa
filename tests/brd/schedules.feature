Feature: One plain-language Schedule for Actions and independent Checks

  Scenario: SCH-QUOTA-001 — A calendar quota opens one current instance
    Given an Action scheduled five times a day
    When the fifth instance is completed
    Then one next instance belongs to the following local day
    And the plan reports five completed instances in the original day

  Scenario: SCH-WINDOW-002 — A week covered in part plans its share of the quota
    Given a Check scheduled three times a week without a clock
    When the Advisor queries Wednesday through Sunday of one week, and then Monday and Tuesday of it
    Then 2 answers are planned for the five days and 1 for the two: the quota times the days covered, divided by 7, rounded
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
    And while a Schedule is not compiled, its Action or Check cannot be finished, and the refusal says why and that off clears it

  Scenario: SCH-CLARIFY-005 — An ambiguous Schedule asks once per revision
    Given a Schedule whose periodicity is incomplete
    When the Scheduler requests clarification
    Then the Advisor is asked once, naming each item as #id «title» with its kind, its Schedule and the question
    And hourly recovery does not parse the same ambiguous text again

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

  Scenario: SCH-STAGE-013 — A generated Action opens where its next slot falls
    Given an Action with a compiled Schedule is finished and its next copy opens
    When a Sprint is running
    Then a copy due today opens in Today, and one due later by the Sprint's planned last day opens in Sprint
    And a weekly quota's copy opens in Sprint while its week starts by the Sprint's last day
    And a copy due after the Sprint's last day opens in Backlog
    And an after-completion copy keeps Today or Sprint, and one from Backlog opens in Sprint
    When no Sprint is running
    Then a copy from Backlog stays in Backlog
    And a copy from Sprint or Today opens in Today when due today, keeps its stage when it repeats after completion, and otherwise opens in Sprint

  Scenario: SCH-RETRY-014 — A compilation that failed is tried again
    Given the model could not be reached while a Schedule was compiled
    Then the Schedule stays waiting to be set up, and its Action or Check cannot be finished yet
    And it is compiled again when Safwa starts and each hour, until it is read or asks a question

  Scenario: SCH-LIMIT-015 — An Action repeats at most ten times a day
    Given a Schedule on an Action that plans more than 10 executions a day (ACTION_DAILY_EXECUTIONS_MAX = 10)
    Then the Scheduler asks whether to make it a Check instead or choose fewer, rather than saving it
    And a daily quota of 11, a weekly quota of 71 and an interval under 144 minutes are each asked about
    And a Check with the same Schedule is saved

  Scenario: SCH-EDITOR-016 — A Schedule typed into an editor is read before it is saved
    Given the owner types a Schedule on an Action's, a Check's or a Goal's screen, or into a Card draft
    Then the Scheduler reads it before anything is written, and the screen shows how it was read
    And a question from the Scheduler is shown on the same editor, which keeps waiting, and nothing is saved
    And off clears the Schedule without asking the model
    And a draft saved after its Schedule was read creates the Card with that Schedule ready
