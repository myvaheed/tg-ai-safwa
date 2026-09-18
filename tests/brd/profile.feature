Feature: Profile
  Profile is where the owner tells Safwa things outright, rather than leaving Safwa to infer them.
  There are nine of them, each edited on its own, each checked before it is stored.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace whose Profile holds the nine things the owner can tell Safwa outright

  Scenario: PS-CONTEXT-001 — What the owner said outright outranks what Safwa remembered
    Given memory.md and the Profile say different things about the owner
    When Safwa is given its context
    Then what it remembered comes first
    And About me and Advisor instructions come after it, so they are what it goes by

  Scenario: PS-FIELD-002 — Profile writes only the fields it has
    Given the nine fields
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

  Scenario: PS-CLOCK-005 — A time of day is a wall clock
    Given a time from 00:00 through 23:59, for memory upkeep, the Diary or the daily summary
    Then that local time is accepted
    And for memory upkeep, off means there is no time, so nothing is scheduled
    And for the Diary and the daily summary off is refused: each is an automatic reaction with a switch of its own (PS-HOOKS-015)
    But anything else typed in that box is refused

  Scenario: PS-DIARY-006 — The Diary time and prompt are what Safwa's own Diary nudge follows
    Given a Diary time (DIARY_TIME_DEFAULT = "22:00") and a Diary prompt in the Profile
    When the Diary time passes and the chat is free
    Then the Advisor is asked once to call the diary subagent for today and propose what it reports, with the Diary prompt after it when there is one
    And the time is read at every look and the prompt when the request is about to be said, so a change to either counts without a restart, by AG-HOOK-039
    And no Reminder stands behind it: the owner's own Reminders and a Sprint's end warnings are untouched by either

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

  Scenario: PS-SUMMARY-014 — The summary time is what Safwa's own daily summary follows
    Given a new workspace, whose summary time is 20:00 (SUMMARY_TIME_DEFAULT = "20:00")
    When the summary time passes and the chat is free
    Then the Advisor is asked once to tell the owner what they got done that day — the Actions finished, the Checks resolved and the Diary entry of the day — and to offer to write the day down when there is no entry, unless the Diary request came with this one
    And the time is read at every look, so one moved in the Profile counts from the next time it passes, by AG-HOOK-039
    And when the summary and the Diary fall due together they reach Safwa as one request, so
      the owner gets one message

  Scenario: PS-HOOKS-015 — An automatic reaction that reaches the Advisor is switched in the Profile
    Given the application registers its automatic reactions
    When Profile is drawn
    Then each reaction that hands the Advisor a request or a helper is on the screen by its title, with its description, and is on
    And one that runs work of its own, such as the automatic Summary, is not on the screen and is always on
    When the owner presses one
    Then that reaction is off from that moment, without a restart, and stays off after one
    And pressing it again turns it back on
    And while it is off its condition is not checked

  Scenario: PS-MORNING-016 — The Morning time is when Safwa's morning checks run
    Given a new workspace, whose Morning time is 09:00 (MORNING_TIME_DEFAULT = "09:00")
    Then it is on the Profile with the other clocks, edited as a time from 00:00 through 23:59
    And off is refused: each morning check has a switch of its own (PS-HOOKS-015)
    When the Morning time changes
    Then the morning checks — Goals without Actions (CD-EMPTY-035), Hard Time outside the plan (PL-HARDTIME-021) and Rest in Today (CD-REST-037) — run at the new time from the next time it passes, without a restart
