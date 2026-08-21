Feature: Profile and Settings
  Explicit owner settings override inferred memory and are edited through one validated screen.

  Scenario: PS-CONTEXT-001 - Explicit Profile context follows memory.md
    Given memory.md and the Profile contain conflicting owner context
    When provider context is assembled
    Then Persistent memory appears first
    And About me and Advisor instructions appear later

  Scenario: PS-FIELD-002 - Undeclared Profile fields are rejected
    Given the seven declared Profile fields
    When an update names any other field
    Then the update fails without changing Profile data or workspace revision

  Scenario: PS-SPRINT-LENGTH-003 - Sprint length accepts 2 through 60 days
    Given Sprint length is a whole number from 2 through 60 inclusive
    Then the value is accepted
    But 1 and 61 are rejected without changing the Profile

  Scenario: PS-CAPACITY-004 - Sprint capacity accepts positive points or off
    Given Sprint capacity is a whole number of at least 1 effort point
    Then the value is accepted
    And off stores no capacity
    But 0, negative values, and non-integers are rejected

  Scenario: PS-CLOCK-005 - Memory and Diary clocks accept HH:MM or off
    Given a Memory or Diary clock value from 00:00 through 23:59
    Then that local time is accepted
    And off stores no scheduled time
    But any other clock text is rejected

  Scenario: PS-DIARY-006 - Diary Reminder Settings synchronize the Diary System Reminder
    Given ordinary owner Reminders and Sprint-linked Reminders may also exist
    When Diary Reminder time or instruction changes
    Then the Diary System Reminder with system true and no Sprint is synchronized
    And Diary time off removes only that System Reminder

  Scenario: PS-UI-SAVE-008 - Valid input updates the selected field and auto-closes its prompt
    Given one Settings field prompt is open
    When its input is valid
    Then only the selected Profile field changes
    And the prompt auto-closes
    And Settings redraws

  Scenario: PS-UI-INVALID-009 - Invalid input keeps data and the same prompt
    Given one Settings field prompt is open
    When its input is invalid
    Then Profile data is unchanged
    And the same field prompt remains open with its validation message

  Scenario: PS-TIMEZONE-010 - Settings displays timezone without an edit action
    Given the workspace timezone is Europe/Istanbul
    When Settings is rendered
    Then Europe/Istanbul is visible
    And no timezone edit action exists

  Scenario: PS-REVISION-011 - One Profile update bumps revision once
    Given the current workspace revision
    When one Profile update succeeds
    Then workspace revision increases by exactly one
