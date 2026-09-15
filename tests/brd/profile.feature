Feature: Profile
  Profile is where the owner tells Safwa things outright, rather than leaving Safwa to infer them.
  There are eight of them, each edited on its own, each checked before it is stored.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace whose Profile holds the eight things the owner can tell Safwa outright

  Scenario: PS-CONTEXT-001 — What the owner said outright outranks what Safwa remembered
    Given memory.md and the Profile say different things about the owner
    When Safwa is given its context
    Then what it remembered comes first
    And About me and Advisor instructions come after it, so they are what it goes by

  Scenario: PS-FIELD-002 — Profile writes only the fields it has
    Given the eight fields
    When anything tries to write a name that is not one of them
    Then it is refused, no field changes, and nothing is recorded as having changed

  Scenario: PS-SPRINT-LENGTH-003 — A Sprint is between 2 and 60 days long
    Given a whole number of days from 2 through 60
      (SPRINT_LENGTH_MIN_DAYS = 2, SPRINT_LENGTH_MAX_DAYS = 60)
    Then it is accepted
    But 1 day and 61 days are refused, and the field keeps what it had

  Scenario: PS-CAPACITY-004 — Sprint capacity is a real number of points, or off
    Given a positive number of effort points, a half included
    Then it is accepted
    And off means the owner is not committing to a capacity at all
    But zero and a negative number are refused

  Scenario: PS-CLOCK-005 — A time of day is a wall clock, or off
    Given a time from 00:00 through 23:59, for memory upkeep, the Diary or the daily summary
    Then that local time is accepted
    And off means there is no time, so nothing is scheduled
    But anything else typed in that box is refused

  Scenario: PS-DIARY-006 — The Diary time and prompt are what Safwa's own Diary Reminder follows
    Given the owner also has Reminders of their own, and a Sprint may have its end warnings
    When the Diary time or the Diary prompt changes
    Then Safwa's own Diary Reminder is changed to match, and it is the only one changed
    And turning the Diary time off deletes that one Reminder and leaves every other one alone

  Scenario: PS-UI-SAVE-008 — A valid answer saves that one field and closes its prompt
    Given one Profile prompt is open
    When the owner types something valid
    Then only the field they were asked for changes
    And the prompt closes itself and Profile is redrawn with the new value, by SC-INPUT-008

  Scenario: PS-UI-INVALID-009 — A rejected answer changes nothing and asks again
    Given one Profile prompt is open
    When the owner types something invalid
    Then no field changes
    And the same prompt is still there, now saying what was wrong with it, by SC-INPUT-008

  Scenario: PS-TIMEZONE-010 — The timezone is shown but not editable here
    Given the workspace timezone is Europe/Istanbul
    When Profile is drawn
    Then Europe/Istanbul is on the screen
    And there is no button to change it

  Scenario: PS-REVISION-011 — One saved field counts as one change
    Given the workspace carries a change count that anything pending is checked against
    When one field is saved
    Then that count goes up by exactly one, so one edit never looks like two

  Scenario: PS-DIARY-012 — The Diary Reminder is due at the next Diary time, never one in the past
    Given it is 23:50 and the Diary time is 07:30
    When Safwa's own Diary Reminder is worked out
    Then it is due at 07:30 tomorrow, not at 07:30 that has already gone

  Scenario: PS-DIARY-013 — Startup works it out again, so a change made while Safwa was down counts
    Given the timezone changed while Safwa was not running
    When Safwa starts
    Then its own Diary Reminder exists and is due at the Diary time in the timezone that is now set

  Scenario: PS-SUMMARY-014 — The summary time is what Safwa's own daily summary Reminder follows
    Given a new workspace, whose summary time is 20:00 (SUMMARY_TIME_DEFAULT = "20:00")
    When the summary time changes
    Then Safwa's own daily summary Reminder is changed to match, and it is the only one changed
    And turning the summary time off deletes that one Reminder and leaves the Diary's and every
      other one alone
    And when the summary and the Diary fall due together they reach Safwa as one request, so
      the owner gets one message

  Scenario: PS-HOOKS-015 — An automatic reaction with a switch is switched in the Profile
    Given the application registers its automatic reactions, and automatic Summary has a switch
    When Profile is drawn
    Then each reaction with a switch is on the screen by its title, with its description, and is on
    And a reaction without one, such as the Heavy analyzer offer, is not on the screen and is always on
    When the owner presses one
    Then that reaction is off from that moment, without a restart, and stays off after one
    And pressing it again turns it back on
    And while it is off its condition is not checked, and a result it was still working on
      when it was switched off is not published
