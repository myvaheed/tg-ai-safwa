Feature: Reminders — the record
  A Reminder is instruction text plus a schedule, and nothing else. The owner never structures a
  schedule and neither does the model: timing arrives as plain words, one session resolves those
  words into parameters, and the schedule is computed from them in code.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace with a local timezone and an Advisor that proposes rather than writes

  Scenario: RM-SCHEDULE-001 — Timing reaches the system as free text and is computed in code
    Given the board subagent proposes a Reminder with when in plain words
    When the proposal is prepared
    Then a setup session resolves those words into schedule parameters
    And the schedule itself is computed from those parameters in code
    And the mutation tool accepts no schedule field of any kind

  Scenario: RM-SCHEDULE-002 — A phrase that does not determine a schedule asks the owner
    Given the owner says "remind me every morning"
    When the setup session runs
    Then it answers that the phrase does not determine a schedule, with one question to ask
    And that question comes back to the model as a retryable tool error
    And no proposal row and no Reminder exist

  Scenario: RM-SCHEDULE-003 — A date is always a start date; a time is a fire clock only next to weekdays
    Given resolved parameters
    When the schedule is built
    Then an interval alone starts now and repeats
    And an interval with a time starts at that clock's next occurrence
    And an interval with a date and a time starts at that exact moment
    And weekdays with a time fire weekly at that clock, or daily when all seven are given
    And weekdays with a date fire on the first matching day on or after it
    And a date with a time is a single occurrence
    And a time alone is its next occurrence

  Scenario: RM-SCHEDULE-004 — A recurrence that started in the past is already running
    Given a repeating schedule whose start is in the past
    When its first fire is computed
    Then it is the next occurrence from now
    And no missed occurrence is created

  Scenario: RM-SCHEDULE-004 — A single occurrence in the past cannot be satisfied
    Given a single occurrence whose moment has already passed
    When the schedule is built
    Then it is refused, because that moment cannot be satisfied

  Scenario: RM-SCHEDULE-005 — Parameters that cannot become a schedule are named, never corrected
    Given parameters that contradict each other or fall outside the allowed range
    When the schedule is built
    Then the reason is stated and no schedule is produced
    And an interval below 5 minutes (REMINDER_MIN_INTERVAL_MINUTES), weekdays given together with
      an interval, quiet windows on a schedule that is not an interval, quiet windows leaving no
      time of day, a date without an hour, and nothing at all are each such a reason

  Scenario: RM-CLOCK-006 — A stored fire time is a local wall clock, not a UTC offset
    Given a daily Reminder at 08:30 local
    When the local zone crosses a daylight-saving shift
    Then the next fire is still 08:30 local
    And a quiet window edge lands at the same local clock on both sides of the shift

  Scenario: RM-QUIET-007 — A quiet window suppresses hours of the day, and its end is exclusive
    Given an interval Reminder with a quiet window
    When a computed fire lands inside that window
    Then it moves to the window's end
    And a window written as one range wrapping midnight and the same window written as two ranges
      meeting at 00:00 behave identically
    And chained windows push a candidate through all of them

  Scenario: RM-WRITE-008 — No Reminder is written without a proposal
    Given the model proposes a Reminder
    When the owner sees the review screen
    Then it names the resolved schedule rather than the words the owner used
    And Save creates the Reminder with exactly that schedule and its first fire
    And Discard leaves no Reminder and no trace of one

  Scenario: RM-WRITE-009 — An edit that carries no timing leaves the schedule alone
    Given a Reminder the model is editing
    When it sends new instruction text and no timing
    Then no setup session runs and no schedule column changes

  Scenario: RM-WRITE-009 — Editing the text by hand leaves the schedule alone
    Given the owner opens a Reminder and edits its text
    When they send the new text
    Then the Reminder keeps its next fire
    And empty text is refused and the Reminder view comes back unchanged

  Scenario: RM-WRITE-010 — Deletion is the only off switch
    Given a Reminder
    When it is removed, by the owner from its screen or by an approved model proposal
    Then the row is gone and it stops firing immediately
    And there is no archive and no disabled state to return from
    And removal is one confirmation, not the permanent-deletion screen a Card tree gets
